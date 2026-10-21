#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import platform
import re
import secrets
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, TracebackType
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
if (HERE.parent / "vendor").is_dir() and str(HERE.parent / "vendor") not in sys.path:
    sys.path.append(str(HERE.parent / "vendor"))

import yaml
from faker_kit_andon import LABEL, MACHINE, Andon, AndonError, Cord, clean_reason, issue_body
from faker_kit_cloud import CHECKOUT, Cloud, CloudError
from faker_kit_env import EnvFileError, parse_env_file, set_env, use_bot_env
from faker_kit_gh import NAME, Gh, Refused, Runner, run_gh
from faker_kit_guard.policy import (
    DEFAULT_HEARTBEAT_EVERY,
    DEFAULT_INCIDENTS_EVERY,
    DEFAULT_SECURITY_EVERY,
    INTERVAL,
    REPO,
    RESERVED_NAMES,
    TAINTED_TURN_SECONDS,
    Bot,
    Policy,
    PolicyError,
    faker_kit_home,
    load_policy,
    parse_policy,
    tainted_turn_marker,
)
from faker_kit_incident_sources import FetchJson, fetch_json
from faker_kit_incidents import FetchStatus, IncidentStore, Watch, fetch_status
from faker_kit_issues import LABELS, IssueStore, Reports
from faker_kit_loop import Loop, LoopError
from faker_kit_machine import LOGIN, WEBHOOK_HOST, WEBHOOK_PORT, Machine, MachineError, check_model_choice, edit_guard, load_machine, route_url, routes, secret_env
from faker_kit_merge import MERGE_METHOD, NOTICE, REVIEW_WORKFLOW, SECURITY_JOB, WINDOW_SECONDS, Evidence, WindowStore, decide, dispatch_rules, from_fork, security_run_id
from faker_kit_profiles import Profiles
from faker_kit_runner import RunnerError, Services, Slot, build_image, check_slot, up
from install_config import InstallError, config_set, hermes_send

VENDOR_MANIFEST = ".agents/faker-kit/KIT.json"
KIT_BRANCH_PREFIX = "faker-kit/kit-"
BRIEF_START = "/faker-mode"
MAX_BRIEF_CHARS = 60_000
SESSION_MODEL = re.compile(r"\A(?:opus|sonnet|haiku|fable|claude-[a-z0-9]+(?:-[a-z0-9]+){1,6})\Z")
SESSION_EFFORTS = ("low", "medium", "high", "xhigh", "max")
UNIT_LINE = re.compile(r"\AUnit: ([a-z0-9][a-z0-9-]{0,63})[ \t]*\r?(?:\n|\Z)")
STATUS_PRS = 30
OWNER_PLACEHOLDER = "owner-login"
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
SECRET_SHAPES = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
ANDON_FOOTER = (
    "\n\nStop rule: before every push, run `gh api 'repos/{repo}/issues?labels=agent:andon&state=open' --jq length`. "
    "If it prints anything but 0, do not push or update a pull request; say so and end the session.\n"
)
ROLE_COMMANDS = {
    "project": {"launch", "merge", "status", "andon", "incidents", "issues"},
    "bootstrap": {"add-project", "vendor", "launch", "status", "andon"},
    "chief": {"status", "handoff", "loop"},
}
OWNER_ONLY = {"install", "runner"}
ANDON_COLOR = "b60205"
ANDON_DESCRIPTION = "The andon cord: agents stop, and nothing new starts until the owner clears it in Hermes"
SCHEDULE_DEFAULTS = {"heartbeat_every": DEFAULT_HEARTBEAT_EVERY, "incidents_every": DEFAULT_INCIDENTS_EVERY, "security_every": DEFAULT_SECURITY_EVERY}


def current_bot() -> str:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.name if home.parent.name == "profiles" else "default"


def check_name(value: str, what: str) -> None:
    if not NAME.match(value) or value in RESERVED_NAMES:
        raise Refused(f"{what} {value!r} must be lowercase letters, digits, - and _, and not default")


def check_repo(value: str) -> None:
    if not REPO.match(value):
        raise Refused(f"{value!r} is not OWNER/REPO")


def check_interval(value: str | None, what: str) -> None:
    if value is not None and not INTERVAL.match(value):
        raise Refused(f"{what} must be a positive interval like 10m or 1h")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")[:63]


class Kit:
    def __init__(self, run: Runner = run_gh, cloud: Cloud | None = None, home: Path | None = None, now: Callable[[], float] = time.time,
                 fetch_api: FetchJson = fetch_json, fetch_url: FetchStatus = fetch_status) -> None:
        self.run, self.now, self.fetch_api, self.fetch_url = run, now, fetch_api, fetch_url
        self.home = home or faker_kit_home()
        self.root = self.home.parent
        self.kit = self.home / "kit"
        self.pkg = self.kit / "hermes"
        self.cloud = cloud or Cloud(run_gh=run)
        self.andon = Andon(self.home)
        self.profiles = Profiles(self.root, self.pkg)
        self.machine_login_known: bool | None = None

    def bots_file(self) -> dict:
        path = self.home / "bots.yaml"
        data = (yaml.safe_load(path.read_text()) if path.exists() else None) or {"version": 1, "bots": {}}
        data.setdefault("bots", {})
        return data

    def write_bots(self, data: dict) -> None:
        self.policy_from(data)
        path = self.home / "bots.yaml"
        tmp = path.with_name(f".bots.yaml.{os.getpid()}")
        tmp.write_text("# Written by faker-kit. Each key is a Hermes profile name.\n" + yaml.safe_dump(data, sort_keys=True, allow_unicode=True))
        tmp.chmod(0o600)
        tmp.replace(path)

    def policy_from(self, bots: dict) -> Policy:
        return parse_policy(yaml.safe_load((self.home / "guard.yaml").read_text()), bots, self.home)

    def policy(self) -> Policy:
        return load_policy(self.home)

    def machine_login(self) -> bool:
        if self.machine_login_known is None:
            try:
                self.run(["auth", "status"])
                self.machine_login_known = True
            except Refused:
                self.machine_login_known = False
        return self.machine_login_known

    def kit_commit(self) -> str:
        done = subprocess.run(["git", "-C", str(self.kit), "rev-parse", "HEAD"], capture_output=True, text=True)
        if done.returncode != 0:
            raise Refused(f"no kit checkout at {self.kit}; run hermes/install.sh")
        return done.stdout.strip()

    def api(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        args = ["api", "-X", method, path] if method != "GET" else ["api", path]
        out = self.run(args) if body is None else self.run([*args, "--input", "-"], input=json.dumps(body))
        return json.loads(out) if out.strip() else {}

    def project_bot(self, policy: Policy, project: str) -> Bot:
        bot = policy.projects().get(project)
        if bot is None:
            raise Refused(f"no project {project}; create it with add-project first")
        return bot

    def refuse_after_untrusted_text(self, bot: str, action: str) -> None:
        folder = tainted_turn_marker(self.home, bot)
        for record in sorted(folder.iterdir()) if folder.is_dir() else []:
            if record.name.startswith("."):
                continue
            try:
                at = float(json.loads(record.read_text())["at"])
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError):
                raise Refused(f"{action} could not read this bot's tainted-turn record; ask again in a new message") from None
            if self.now() - at < TAINTED_TURN_SECONDS:
                raise Refused(f"this bot read text strangers wrote in its current turn, so it may not {action}; the owner or the chief of staff asks again in a new message")

    def cord(self, bot: str | None) -> Cord | None:
        return self.andon.pulled(bot)

    def brief_text(self, owner: str, path: str) -> str:
        folder = self.home / "briefs" / owner
        given = Path(path).expanduser()
        if folder.is_symlink() or given.is_symlink():
            raise Refused("a brief and its folder must be real files, not links")
        briefs = folder.resolve()
        target = given.resolve()
        if target.parent != briefs or target.suffix != ".md":
            raise Refused(f"--brief-file must be a .md file in {briefs}")
        try:
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            raise Refused(f"cannot read {target}; a brief must be a regular file, not a link") from None
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode) or os.fstat(handle.fileno()).st_nlink != 1:
                raise Refused(f"{target} must be a regular file with no other names")
            return handle.read()

    def launch(self, caller: Bot | None, project: str | None, brief_file: str, model: str | None = None, effort: str | None = None) -> dict:
        if model is not None and not SESSION_MODEL.match(model):
            raise Refused("--model takes opus, sonnet, haiku, fable, or a full claude- model name")
        if effort is not None and effort not in SESSION_EFFORTS:
            raise Refused(f"--effort takes one of {', '.join(SESSION_EFFORTS)}")
        if caller is not None and caller.role == "project":
            target = caller
        elif project:
            target = self.project_bot(self.policy(), project)
        else:
            raise Refused("name the project: launch --project P --brief-file F")
        text = self.brief_text(caller.name if caller else "default", brief_file).strip()
        if not text.startswith(BRIEF_START):
            raise Refused(f"the brief must start with {BRIEF_START}, so the session works in faker-mode")
        if len(text) > MAX_BRIEF_CHARS:
            raise Refused(f"the brief is longer than {MAX_BRIEF_CHARS} characters; write the detail into a file in the repository and point to it")
        if SECRET_SHAPES.search(text):
            raise Refused("the brief looks like it holds a token or key; a session's first message is a process argument, so no secret goes in it")
        repo = str(target.repo)
        self.kit_matches(Gh(self.home, target.name, repo, self.run), repo)
        cord = self.cord(target.name)
        if cord:
            raise Refused(f"{cord.describe()}; nothing starts until it is cleared")
        try:
            use_bot_env(self.root / "profiles" / target.name)
        except EnvFileError as err:
            raise Refused(str(err)) from None
        prompt = text + ANDON_FOOTER.format(repo=repo)
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        owner, name = repo.split("/")
        workdir = self.home / "checkouts" / f"{owner}__{name}" / f"launch-{int(self.now())}-{digest}"
        try:
            session = self.cloud.start(repo, prompt, workdir, model, effort)
        except CloudError as err:
            raise Refused(f"launch failed: {err}") from err
        return {"repo": repo, "session": session, "brief": digest, "model": model, "effort": effort}

    def kit_matches(self, gh: Gh, repo: str) -> None:
        local = self.kit_commit()
        try:
            vendored = json.loads(base64.b64decode(gh.json(f"repos/{repo}/contents/{VENDOR_MANIFEST}")["content"])).get("kit_ref")
        except (Refused, ValueError, KeyError, TypeError, AttributeError):
            raise Refused(f"{repo} has no vendored kit, so cloud sessions would start without its skills; run faker-kit vendor --project <project>") from None
        if vendored != local:
            raise Refused(f"the vendored kit {str(vendored)[:12]} differs from this machine's kit {local[:12]}; run faker-kit vendor --project <project>")

    def evidence(self, gh: Gh, pr: int, head: str, default: str) -> Evidence:
        statuses = gh.statuses(head)
        run_id = security_run_id(statuses, gh.repo)
        try:
            rules = dispatch_rules(gh.file_text(".agents/dispatch.json", default))
        except ValueError as err:
            raise Refused(str(err)) from None
        return Evidence(gh.check_runs(head), statuses, gh.reviews(pr), gh.pull_files(pr), rules,
                        gh.actions_run(run_id) if run_id else None, gh.actions_jobs(run_id) if run_id else None,
                        gh.head_pulls(head), gh.head_jobs(head, REVIEW_WORKFLOW, SECURITY_JOB))

    def ping_owner(self, bot: Bot, pr: int) -> None:
        repo = str(bot.repo)
        text = f"PR #{pr} in {repo} merges in {WINDOW_SECONDS // 3600} hour unless you request changes: https://github.com/{repo}/pull/{pr}"
        if bot.slack_channel:
            try:
                hermes_send(bot.name, f"slack:{bot.slack_channel}", text)
            except InstallError as err:
                raise Refused(f"the Slack message to the owner was not delivered ({err})") from None
            return
        authors = sorted(self.policy().trusted_authors)
        if not authors or any(not LOGIN.match(login) or login == OWNER_PLACEHOLDER for login in authors):
            raise Refused("the bot has no Slack channel and guard.yaml trusted_authors does not list the owner's GitHub login")
        owners = " ".join(f"@{login}" for login in authors)
        try:
            self.api(f"repos/{repo}/issues/{pr}/comments", "POST", {"body": f"{owners} {text}"})
        except Refused as err:
            raise Refused(f"the pull request comment to the owner was not posted ({err})") from None

    def merge(self, bot: Bot, pr: int) -> dict:
        self.refuse_after_untrusted_text(bot.name, "merge")
        repo = str(bot.repo)
        gh = Gh(self.home, bot.name, repo, self.run)
        data = gh.pull(pr)
        head = str((data.get("head") or {}).get("sha") or "")
        if not SHA.match(head):
            raise Refused(f"#{pr} has no head commit GitHub reports")
        if from_fork(data, repo):
            raise Refused(f"#{pr} comes from a fork; only the owner merges or closes it on GitHub")
        if data.get("state") != "open" or data.get("merged_at"):
            raise Refused(f"#{pr} is already {'merged' if data.get('merged_at') else str(data.get('state'))}")
        default = gh.default_branch()
        if (data.get("base") or {}).get("ref") != default:
            raise Refused(f"#{pr} targets another branch than the default branch {default}; only the owner merges it on GitHub")
        if data.get("mergeable") is False:
            raise Refused(f"#{pr} conflicts with its base branch; its author must update it first")
        if data.get("mergeable") is not True:
            raise Refused(f"GitHub is still checking #{pr} for conflicts; run merge again in a minute")
        cord = self.cord(bot.name)
        if cord:
            raise Refused(f"{cord.describe()}; nothing merges until the owner clears it")
        store = WindowStore(self.home, bot.name)
        with store.locked():
            verdict = decide(pr, head, self.evidence(gh, pr, head, default), repo, default, store.get(pr), self.now())
            if verdict.window is not None:
                try:
                    self.ping_owner(bot, pr)
                except Refused as err:
                    print(f"{NOTICE} PR #{pr} did not start its merge window because the owner could not be told")
                    raise Refused(f"every gate passed, but the owner could not be told: {err}; #{pr}'s window did not start, so run merge again once the owner can be reached") from None
                store.put(pr, verdict.window)
                if verdict.notice:
                    print(verdict.notice)
                raise Refused(f"every gate passed; the owner was told and the {WINDOW_SECONDS // 60}-minute window for #{pr} has started")
            if verdict.action != "merge":
                raise Refused(verdict.message)
            self.run(["api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/merge", "-f", f"merge_method={MERGE_METHOD}", "-f", f"sha={head}"])
            store.put(pr, None)
        return {"pr": pr, "merged": True, "head": head, "method": MERGE_METHOD}

    def incidents(self, bot: Bot, ack: str | None) -> dict:
        try:
            env = parse_env_file(self.root / "profiles" / bot.name / ".env")
        except EnvFileError as err:
            raise Refused(str(err)) from None
        watch = Watch(Gh(self.home, bot.name, str(bot.repo), self.run), IncidentStore(self.home, bot.name), self.policy().alert_hosts, env, SECRET_SHAPES,
                      self.fetch_api, self.fetch_url, self.now)
        return watch.acknowledge(ack) if ack else watch.run()

    def issues(self, bot: Bot, claim: int | None) -> dict:
        reports = Reports(Gh(self.home, bot.name, str(bot.repo), self.run), IssueStore(self.home, bot.name))
        return reports.claim(claim) if claim else {"issues": reports.eligible()}

    def status(self, caller: Bot | None, everything: bool) -> list[str]:
        policy = self.policy()
        if everything or caller is None or caller.role != "project":
            machine = self.andon.read(MACHINE)
            lines = [f"ANDON for the machine: {machine.reason}. Nothing new starts in any project."] if machine else []
            for bot in sorted(policy.projects().values(), key=lambda b: b.project or ""):
                lines.append(self.project_line(bot))
            return lines or ["no projects"]
        gh = Gh(self.home, caller.name, str(caller.repo), self.run)
        store = WindowStore(self.home, caller.name)
        default = gh.default_branch()
        own = [pull for pull in gh.open_pulls() if not from_fork(pull, gh.repo)]
        return [self.pr_line(gh, store, pull, default) for pull in own[:STATUS_PRS]] or ["no open pull requests"]

    def project_line(self, bot: Bot) -> str:
        cord = self.andon.read(bot.name)
        head = f"{bot.project} ({bot.name}, {bot.repo})" + (f" ANDON: {cord.reason}" if cord else "")
        try:
            pulls = Gh(self.home, bot.name, str(bot.repo), self.run).open_pulls()
        except Refused as err:
            return f"{head}: unreadable: {err}"
        drafts = sum(1 for p in pulls if p.get("draft"))
        return f"{head}: open {len(pulls)} (draft {drafts}, ready {len(pulls) - drafts})"

    def pr_line(self, gh: Gh, store: WindowStore, pull: dict, default: str) -> str:
        number = int(pull["number"])
        detail = gh.pull(number)
        head = str((detail.get("head") or {}).get("sha") or "")
        who = re.sub(r"[^A-Za-z0-9_\[\]-]", "?", str((pull.get("user") or {}).get("login") or "ghost"))[:40]
        verdict = decide(number, head, self.evidence(gh, number, head, default), gh.repo, default, store.get(number), self.now())
        gate = {"merge": "ready to merge", "wait": verdict.message, "refuse": f"blocked: {verdict.message}"}[verdict.action]
        mergeable = {True: "mergeable", False: "conflicts", None: "mergeable unknown"}[detail.get("mergeable")]
        unit = UNIT_LINE.match(str(detail.get("body") or ""))
        return " | ".join([f"#{number}", *([f"unit {unit.group(1)}"] if unit else []), f"by {who}", "draft" if pull.get("draft") else "ready", mergeable, gate])

    def handoff(self, caller: Bot, project: str, part: str) -> dict:
        self.refuse_after_untrusted_text(caller.name, "hand out work")
        owner = self.policy().projects().get(project)
        if owner is None:
            raise Refused(f"no project {project} on this machine; faker-kit status --all lists them")
        return Loop(self.home, caller.name).add(project, owner.name, part, self.now())

    def loop(self, caller: Bot, action: str, part_id: int | None, state: str | None, prs: list[int], waits_on: str | None, reported: bool,
             everything: bool) -> dict:
        loop = Loop(self.home, caller.name)
        if action == "set":
            if part_id is None:
                raise Refused("loop set takes the part's id: faker-kit loop set <id> --state <state>")
            return loop.update(part_id, state, prs, waits_on, reported, self.now())
        if part_id is not None or state or prs or waits_on or reported:
            raise Refused(f"loop {action} takes no part id or changes; use loop set <id>")
        return loop.digest(self.now()) if action == "digest" else loop.show(everything)

    def andon_command(self, caller: Bot | None, action: str, words: list[str]) -> dict:
        project = caller is not None and caller.role == "project"
        name = caller.name if project and caller else MACHINE
        scope = "project" if project else "machine"
        repos = [str(caller.repo)] if project and caller else sorted(str(b.repo) for b in self.policy().projects().values())
        current = self.andon.read(name)
        if action != "raise" and words:
            raise Refused(f"andon {action} takes no reason")
        if action == "show":
            other = self.andon.read(MACHINE) if project else None
            return {"stopped": bool(current or other), "scope": scope, "reason": current.reason if current else None,
                    "machine": other.reason if other else None}
        if action == "raise":
            why = clean_reason(words)
            if current:
                raise Refused(f"{current.describe()}; clear it before pulling it again")
            cord = Cord(scope, why, caller.name if caller else "the owner", self.now(), {})
            self.andon.write(name, cord)
            issues: dict[str, int] = {}
            failed: dict[str, str] = {}
            for repo in repos:
                try:
                    url = self.run(["issue", "create", "-R", repo, "--title", f"Andon: {why}"[:120], "--label", LABEL,
                                    "--body", issue_body(scope, why, "faker-kit andon clear")]).strip()
                    issues[repo] = int(url.rstrip("/").rsplit("/", 1)[-1])
                except (Refused, ValueError) as err:
                    failed[repo] = str(err)[:200]
                self.andon.write(name, Cord(scope, why, cord.by, cord.at, issues))
            return {"stopped": True, "reason": why, "issues": issues, **({"no_issue": failed} if failed else {})}
        if current is None:
            raise Refused(f"this {scope}'s andon cord is not pulled")
        closed = []
        for repo, number in sorted(current.issues.items()):
            try:
                self.run(["issue", "close", str(number), "-R", repo, "--comment", "The owner cleared the andon cord in Hermes. The line runs again."])
                closed.append(f"{repo}#{number}")
            except Refused:
                continue
        self.andon.drop(name)
        return {"stopped": False, "closed": closed}

    def vendor_module(self) -> ModuleType:
        path = self.kit / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts" / "vendor_kit.py"
        if not path.is_file():
            raise Refused(f"the kit at {self.kit} has no vendor_kit.py; pin a newer kit commit")
        spec = importlib.util.spec_from_file_location("faker_kit_vendor_kit", path)
        if spec is None or spec.loader is None:
            raise Refused(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = writes
        return module

    def vendor(self, project: str) -> dict:
        repo = str(self.project_bot(self.policy(), project).repo)
        vk = self.vendor_module()
        try:
            kit_ref = vk.kit_commit(self.kit)
            plan = vk.plan(self.kit, kit_ref)
        except vk.VendorError as err:
            raise Refused(str(err)) from None
        default = self.api(f"repos/{repo}")["default_branch"]
        ref_path, ref_query = quote(default, safe="/"), quote(default, safe="")
        base = self.api(f"repos/{repo}/git/ref/heads/{ref_path}")["object"]["sha"]
        base_tree = self.api(f"repos/{repo}/git/commits/{base}")["tree"]["sha"]
        listing = self.api(f"repos/{repo}/git/trees/{base_tree}?recursive=1")
        if listing.get("truncated"):
            raise Refused(f"{repo} is too large to list through the API; run vendor_kit.py --into in a clone instead")
        existing = {e["path"]: e for e in listing.get("tree") or []}
        link_dirs = {place for places in vk.LINK_DIRS.values() for place, _ in places}
        for link_dir in sorted(link_dirs):
            if existing.get(link_dir, {}).get("mode") == "120000":
                raise Refused(f"{link_dir} in {repo} is a symlink; make it a real folder first, so the kit's relative links resolve")
        targets: dict[str, bool] = {}

        def ours(entry: dict) -> bool:
            if entry.get("mode") != "120000":
                return False
            if entry["sha"] not in targets:
                blob = self.api(f"repos/{repo}/git/blobs/{entry['sha']}")
                targets[entry["sha"]] = vk.points_into_vendor(base64.b64decode(blob["content"]).decode())
            return targets[entry["sha"]]

        entries = [{"path": path, "mode": "100755" if executable else "100644", "type": "blob", "content": data.decode()}
                   for path, (data, executable) in sorted(plan.files.items())]
        stale = [path for path, e in existing.items() if e["type"] == "blob"
                 and path.startswith(f"{vk.VENDOR_DIR}/") and path not in plan.files and path != vk.MANIFEST]
        linked, skipped = vk.choose_links(plan.links, lambda path: path in existing and not ours(existing[path]))
        planned = {path for path, _ in linked}
        entries += [{"path": path, "mode": "120000", "type": "blob", "content": target} for path, target in linked]
        stale += [path for path, e in existing.items() if path.rsplit("/", 1)[0] in link_dirs and path not in planned and ours(e)]
        entries += [{"path": path, "mode": existing[path]["mode"], "type": "blob", "sha": None} for path in sorted(stale)]
        entries.append({"path": vk.MANIFEST, "mode": "100644", "type": "blob", "content": plan.manifest(linked).decode()})
        if vk.DISPATCH in existing:
            text = base64.b64decode(self.api(f"repos/{repo}/contents/{vk.DISPATCH}?ref={ref_query}")["content"]).decode()
            try:
                moved = vk.move_kit_ref(text, kit_ref)
            except vk.VendorError as err:
                raise Refused(str(err)) from None
            if moved:
                entries.append({"path": vk.DISPATCH, "mode": "100644", "type": "blob", "content": moved})
        tree = self.api(f"repos/{repo}/git/trees", "POST", {"base_tree": base_tree, "tree": entries})["sha"]
        result = {"project": project, "repo": repo, "kit_ref": kit_ref, "files": len(plan.files), "links": len(linked),
                  "kept_repository_own": skipped, "removed": sorted(stale)}
        short = kit_ref[:12]
        branch = f"{KIT_BRANCH_PREFIX}{short}"
        if tree == base_tree:
            superseded = self.close_kit_prs(repo, ref_query, branch, f"{default} already has the kit {short}")
            return {**result, "pr": None, "note": f"{default} already has the kit at {short}", "superseded": superseded}
        commit = self.api(f"repos/{repo}/git/commits", "POST", {"message": f"chore(agents): vendor the kit {short}", "tree": tree, "parents": [base]})["sha"]
        try:
            self.api(f"repos/{repo}/git/ref/heads/{quote(branch, safe='/')}")
            self.api(f"repos/{repo}/git/refs/heads/{quote(branch, safe='/')}", "PATCH", {"sha": commit, "force": True})
        except Refused:
            self.api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": commit})
        mine = [pr["html_url"] for pr in self.kit_pulls(repo, ref_query) if pr["head"]["ref"] == branch]
        if mine:
            url = mine[0]
        else:
            body = (f"Copies the kit at `{kit_ref}` into `.agents/faker-kit/`, and links its skills from `.claude/skills/` and "
                    "`.agents/skills/` and its subagents from `.claude/agents/`, so every agent session, cloud sessions included, "
                    "has them. Generated by `faker-kit vendor`; nothing under `.agents/faker-kit/` is edited by hand.\n\n"
                    f"`python3 .agents/faker-kit/vendor_kit.py --check .` verifies the copy. Kept the repository's own: "
                    f"{', '.join(skipped) or 'none'}. Removed: {', '.join(sorted(stale)) or 'nothing'}.")
            url = self.api(f"repos/{repo}/pulls", "POST", {"title": f"chore(agents): vendor the kit {short}", "head": branch, "base": default, "body": body})["html_url"]
        return {**result, "branch": branch, "pr": url, "superseded": self.close_kit_prs(repo, ref_query, branch, f"{url} vendors the kit {short}")}

    def kit_pulls(self, repo: str, ref_query: str) -> list[dict]:
        pulls = self.run(["api", f"repos/{repo}/pulls?state=open&base={ref_query}&per_page=100"])
        return [pr for pr in json.loads(pulls or "[]")
                if str((pr.get("head") or {}).get("ref", "")).startswith(KIT_BRANCH_PREFIX)
                and str(((pr["head"].get("repo") or {}).get("full_name")) or "").lower() == repo.lower()]

    def close_kit_prs(self, repo: str, ref_query: str, keep: str, why: str) -> list[str]:
        older = [pr for pr in self.kit_pulls(repo, ref_query) if pr["head"]["ref"] != keep]
        for pr in older:
            number = int(pr["number"])
            self.api(f"repos/{repo}/pulls/{number}", "PATCH", {"state": "closed"})
            self.api(f"repos/{repo}/issues/{number}/comments", "POST", {"body": f"Superseded: {why}."})
        return sorted(pr["html_url"] for pr in older)

    def sync_bot(self, bot: Bot, ask_owner: bool, model: str | None, config_dir: str | None, provider: str | None = None) -> dict:
        home = self.profiles.refresh(bot.name, bot.role, ask_owner, model, config_dir, provider)
        out: dict = {"bot": bot.name, "role": bot.role}
        (self.home / "briefs" / bot.name).mkdir(parents=True, exist_ok=True)
        if bot.role == "project":
            if config_dir is None:
                self.profiles.record_config_dir(home, os.environ.get("CLAUDE_CONFIG_DIR", ""))
            intervals = {key: getattr(bot, key) for key in SCHEDULE_DEFAULTS}
            active = self.profiles.github_login(home, self.machine_login())
            out["jobs_created"] = self.profiles.ensure_jobs(bot.name, "project-jobs.md", intervals, bot.slack_channel, active)
            out["jobs_active"] = active
        if bot.role == "chief":
            active = self.profiles.github_login(home, self.machine_login())
            out["jobs_created"] = self.profiles.ensure_jobs(bot.name, "chief-jobs.md", {}, None, active)
            out["jobs_active"] = active
        return out

    def register_project(self, data: dict, repo: str, bot: str, project: str, intervals: dict[str, str | None], slack_channel: str | None) -> None:
        current = data["bots"].get(bot)
        if current and (current.get("role") != "project" or current.get("project") != project):
            raise Refused(f"{bot} is already the {current.get('role')} bot{' of ' + current['project'] if current.get('project') else ''}")
        if current and str(current.get("repo", "")).lower() != repo.lower():
            raise Refused(f"project {project} is {current.get('repo')}; a project has one repository, so start another project for {repo}")
        entry = {"role": "project", "project": project, "repo": current.get("repo") if current else repo}
        for key, value in intervals.items():
            effective = value if value is not None else (current or {}).get(key)
            if effective and effective != SCHEDULE_DEFAULTS[key]:
                entry[key] = effective
        channel = slack_channel if slack_channel is not None else (current or {}).get("slack_channel")
        if channel:
            entry["slack_channel"] = channel
        data["bots"][bot] = entry

    def add_project(self, repo: str, bot: str | None = None, project: str | None = None, model: str | None = None,
                    intervals: dict[str, str | None] | None = None, subscription: bool | None = None, slack_channel: str | None = None,
                    refresh_webhooks: bool = True, provider: str | None = None) -> dict:
        check_repo(repo)
        slug = project or slugify(repo.split("/")[1])
        name = bot or slug
        check_name(name, "bot")
        check_name(slug, "project")
        wanted = intervals or {}
        for key, value in wanted.items():
            check_interval(value, key)
        check_model_choice(provider, model, bool(subscription), "add-project --provider")
        inherited = subscription if subscription is not None else any(self.profiles.subscription_in_use(b) for b in self.bots_file()["bots"])
        use_subscription = not provider and inherited
        config_dir = self.profiles.claude_login() if use_subscription else None
        data = self.bots_file()
        self.register_project(data, repo, name, slug, {key: wanted.get(key) for key in SCHEDULE_DEFAULTS}, slack_channel)
        policy = self.policy_from(data)
        project_bot = policy.bot(name)
        self.run(["label", "create", LABEL, "-R", repo, "--color", ANDON_COLOR, "--description", ANDON_DESCRIPTION, "--force"])
        for name, (color, description) in LABELS.items():
            self.run(["label", "create", name, "-R", repo, "--color", color, "--description", description, "--force"])
        self.write_bots(data)
        out = self.sync_bot(project_bot, policy.ask_owner, model, config_dir, provider)
        owner, repo_name = repo.split("/")
        try:
            checkout = self.cloud.checkout(repo, self.home / "checkouts" / f"{owner}__{repo_name}" / CHECKOUT)
        except CloudError as err:
            raise Refused(f"cannot prepare the launch checkout: {err}") from err
        kept = self.home / "machine.yaml"
        webhook_url = load_machine(kept).webhook_url if refresh_webhooks and kept.exists() else None
        if webhook_url:
            out["webhooks"] = self.webhooks(webhook_url, [(name, repo)])
        steps = [f"The owner trusts the launch checkout once: cd {checkout} && claude, choose yes, then /exit",
                 f"faker-kit vendor --project {slug}, then the owner merges its pull request"]
        if not out["jobs_active"]:
            again = f"{repo} --provider {provider} --model {model}" if provider else repo
            steps.append(f"The owner signs gh in on this machine (gh auth login), then runs faker-kit add-project {again} again to start the jobs")
        return {**out, "project": slug, "repo": repo, "launch_checkout": str(checkout), "next": steps}

    def kit_skill_dirs(self, machine: Machine) -> list[str]:
        wanted = []
        for name in machine.skills:
            folder = self.kit / "plugins" / name / "skills"
            if not folder.is_dir():
                raise Refused(f"skills: the kit has no plugin {name} with skills")
            wanted.append(str(folder))
        return wanted

    def write_private(self, path: Path, text: str) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write(text)
        tmp.replace(path)

    def write_guard(self, text: str) -> None:
        parse_policy(yaml.safe_load(text), self.bots_file(), self.home)
        self.write_private(self.home / "guard.yaml", text)

    def keep_machine(self, config: Path) -> None:
        text = config.read_text()
        kept = self.home / "machine.yaml"
        if not kept.exists() or kept.read_text() != text:
            self.write_private(kept, text)

    def install(self, config: Path | None, bootstrap: str | None, model: str | None, subscription: bool, provider: str | None = None) -> dict:
        machine = load_machine(config) if config else None
        wanted = self.kit_skill_dirs(machine) if machine else []
        use_subscription = subscription or bool(machine and machine.claude_subscription)
        bootstrap = bootstrap or (machine.bootstrap_bot if machine else None)
        if provider and not bootstrap:
            raise Refused("--provider sets the bootstrap bot's provider; name the bot with --bootstrap-bot")
        if not (model or provider) and machine:
            model, provider = machine.bootstrap_model, machine.bootstrap_provider
        choices = [("install --provider", provider, model)]
        if machine:
            choices += [("chief.provider", machine.chief_provider, machine.chief_model)]
            choices += [(f"projects[{i}].provider", p.provider, p.model) for i, p in enumerate(machine.projects)]
        for where, choice_provider, choice_model in choices:
            check_model_choice(choice_provider, choice_model, use_subscription, where)
        if machine:
            self.write_guard(edit_guard((self.home / "guard.yaml").read_text(), machine))
        config_dir = self.profiles.claude_login() if use_subscription else None
        data = self.bots_file()
        for name, role in ((bootstrap, "bootstrap"), (machine.chief if machine else None, "chief")):
            if name:
                check_name(name, "bot")
                if data["bots"].get(name, {}).get("role", role) != role:
                    raise Refused(f"{name} is already a {data['bots'][name]['role']} bot")
                data["bots"][name] = {"role": role}
        self.write_bots(data)
        (self.home / "briefs" / "default").mkdir(parents=True, exist_ok=True)
        projects = []
        for item in machine.projects if machine else ():
            slack = machine.slack.channel_id if machine and machine.slack and machine.slack.bot == item.bot else None
            intervals = {"heartbeat_every": item.heartbeat_every, "incidents_every": item.incidents_every, "security_every": item.security_every}
            projects.append(self.add_project(item.repo, item.bot, item.project, item.model, intervals, use_subscription, slack, refresh_webhooks=False,
                                             provider=item.provider))
        policy = self.policy()
        done = {p["bot"] for p in projects}
        chosen = {bootstrap: (model, provider)}
        if machine and machine.chief:
            chosen[machine.chief] = (machine.chief_model, machine.chief_provider)
        refreshed = []
        for bot in sorted(policy.bots.values(), key=lambda b: b.name):
            if bot.name not in done:
                bot_model, bot_provider = chosen.get(bot.name, (None, None))
                refreshed.append(self.sync_bot(bot, policy.ask_owner, bot_model, config_dir, bot_provider))
        skills = self.profiles.kit_skills(["default", *policy.bots], self.kit / "plugins", wanted) if machine else []
        hooks = self.webhooks(machine.webhook_url, [(p.bot, p.repo) for p in machine.projects]) if machine and machine.webhook_url else []
        if config:
            self.keep_machine(config)
        return {"projects": projects, "refreshed": refreshed, "skills": skills, "webhooks": hooks}

    def webhooks(self, base_url: str, projects: list[tuple[str, str]]) -> list[dict]:
        listener = ("platforms", "webhook")
        config_set(self.root, "default", (*listener, "enabled"), True)
        config_set(self.root, "default", (*listener, "extra", "host"), WEBHOOK_HOST)
        config_set(self.root, "default", (*listener, "extra", "port"), WEBHOOK_PORT)
        env = parse_env_file(self.root / ".env")
        done = []
        for bot, repo in projects:
            key = secret_env(bot)
            secret = env.get(key) or secrets.token_urlsafe(32)
            if env.get(key) != secret:
                set_env(self.root, key, secret)
            for name, route in routes(bot).items():
                for field, value in route.items():
                    config_set(self.root, "default", (*listener, "extra", "routes", name, field), value)
                url = route_url(base_url, bot, name)
                done.append({"repo": repo, "url": url, "hook": self.github_hook(repo, url, route["events"], secret)})
            prefix = route_url(base_url, bot, "")
            for hook in json.loads(self.run(["api", f"repos/{repo}/hooks?per_page=100"]) or "[]"):
                url = str((hook.get("config") or {}).get("url") or "")
                if url.startswith(prefix) and url not in {d["url"] for d in done if d["repo"] == repo}:
                    self.run(["api", "-X", "DELETE", f"repos/{repo}/hooks/{int(hook['id'])}"])
        return done

    def github_hook(self, repo: str, url: str, events: list[str], secret: str) -> str:
        hooks = json.loads(self.run(["api", f"repos/{repo}/hooks?per_page=100"]) or "[]")
        current = next((h for h in hooks if (h.get("config") or {}).get("url") == url), None)
        body = {"active": True, "events": events, "config": {"url": url, "content_type": "json", "insecure_ssl": "0", "secret": secret}}
        if current is None:
            self.run(["api", "-X", "POST", f"repos/{repo}/hooks", "--input", "-"], input=json.dumps({"name": "web", **body}))
            return "created"
        self.run(["api", "-X", "PATCH", f"repos/{repo}/hooks/{int(current['id'])}", "--input", "-"], input=json.dumps(body))
        return "updated"


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="faker-kit")
    sub = top.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("install")
    cmd.add_argument("--config", type=Path)
    cmd.add_argument("--bootstrap-bot")
    cmd.add_argument("--model")
    cmd.add_argument("--provider")
    cmd.add_argument("--claude-subscription", action="store_true")
    cmd = sub.add_parser("add-project")
    cmd.add_argument("repo")
    cmd.add_argument("--bot")
    cmd.add_argument("--project")
    cmd.add_argument("--model")
    cmd.add_argument("--provider")
    cmd.add_argument("--claude-subscription", action="store_true", default=None)
    cmd.add_argument("--heartbeat-every")
    cmd.add_argument("--incidents-every")
    cmd.add_argument("--security-every")
    cmd = sub.add_parser("vendor")
    cmd.add_argument("--project", required=True)
    cmd = sub.add_parser("status")
    cmd.add_argument("--all", action="store_true")
    cmd = sub.add_parser("launch")
    cmd.add_argument("--brief-file", required=True)
    cmd.add_argument("--project")
    cmd.add_argument("--model")
    cmd.add_argument("--effort")
    cmd = sub.add_parser("merge")
    cmd.add_argument("--pr", type=int, required=True)
    cmd = sub.add_parser("issues")
    cmd.add_argument("--claim", type=int)
    cmd = sub.add_parser("incidents")
    cmd.add_argument("--ack")
    cmd = sub.add_parser("handoff")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--part", required=True)
    cmd = sub.add_parser("loop")
    cmd.add_argument("action", nargs="?", choices=["show", "set", "digest"], default="show")
    cmd.add_argument("id", nargs="?", type=int)
    cmd.add_argument("--state")
    cmd.add_argument("--pr", type=int, action="append", default=[])
    cmd.add_argument("--waits-on")
    cmd.add_argument("--reported", action="store_true")
    cmd.add_argument("--all", action="store_true")
    cmd = sub.add_parser("andon")
    cmd.add_argument("action", nargs="?", choices=["show", "raise", "clear"], default="show")
    cmd.add_argument("reason", nargs="*")
    cmd = sub.add_parser("runner")
    steps = cmd.add_subparsers(dest="step", required=True)
    steps.add_parser("build")
    sub_up = steps.add_parser("up")
    sub_up.add_argument("--config", required=True, type=Path)
    sub_slot = steps.add_parser("slot")
    sub_slot.add_argument("--repo", required=True)
    sub_slot.add_argument("--slot", required=True, type=int)
    return top


def run_runner(args: argparse.Namespace) -> object:
    if args.step == "slot":
        check_slot(args.repo, args.slot)
        Slot(args.repo, args.slot).forever()
        return {"slot": "stopped"}
    if args.step == "build":
        return {"image": build_image()}
    return up(args.config, Services(Path.home(), platform.system()), sys.executable, str(Path(__file__).resolve()))


def run_command(args: argparse.Namespace, kit: Kit, who: str) -> object:
    if args.command in OWNER_ONLY and who != "default":
        raise Refused(f"{args.command} runs only from the owner's own shell, never from a bot")
    caller = None if who == "default" else kit.policy().bot(who)
    if caller is not None and args.command not in ROLE_COMMANDS[caller.role]:
        raise Refused(f"{caller.name} is a {caller.role} bot; faker-kit {args.command} is not one of its commands")
    if args.command == "status":
        return kit.status(caller, args.all)
    if args.command == "install":
        return kit.install(args.config, args.bootstrap_bot, args.model, args.claude_subscription, args.provider)
    if args.command == "add-project":
        intervals = {key: getattr(args, key) for key in SCHEDULE_DEFAULTS}
        return kit.add_project(args.repo, args.bot, args.project, args.model, intervals, args.claude_subscription or None, provider=args.provider)
    if args.command == "vendor":
        return kit.vendor(args.project)
    if args.command == "launch":
        return kit.launch(caller, args.project, args.brief_file, args.model, args.effort)
    if args.command == "merge":
        if caller is None or caller.role != "project":
            raise Refused("merge runs in a project bot's chat")
        return kit.merge(caller, args.pr)
    if args.command == "issues":
        if caller is None or caller.role != "project":
            raise Refused("issues runs in a project bot's chat or job")
        return kit.issues(caller, args.claim)
    if args.command == "incidents":
        if caller is None or caller.role != "project":
            raise Refused("incidents runs in a project bot's chat or job")
        return kit.incidents(caller, args.ack)
    if args.command in ("handoff", "loop"):
        if caller is None or caller.role != "chief":
            raise Refused(f"{args.command} runs in the chief of staff's chat or job")
        if args.command == "handoff":
            return kit.handoff(caller, args.project, args.part)
        return kit.loop(caller, args.action, args.id, args.state, args.pr, args.waits_on, args.reported, args.all)
    if args.command == "andon":
        return kit.andon_command(caller, args.action, args.reason)
    return run_runner(args)


def main(argv: list[str] | None = None, run: Runner = run_gh, cloud: Cloud | None = None, bot: str | None = None,
         now: Callable[[], float] = time.time, fetch_api: FetchJson = fetch_json, fetch_url: FetchStatus = fetch_status) -> int:
    args = parser().parse_args(argv)
    try:
        out = run_command(args, Kit(run, cloud, now=now, fetch_api=fetch_api, fetch_url=fetch_url), bot or current_bot())
    except (Refused, CloudError, PolicyError, AndonError, LoopError, MachineError, InstallError, RunnerError, yaml.YAMLError) as err:
        print(f"faker-kit: refused: {err}", file=sys.stderr)
        return 1
    print("\n".join(out) if isinstance(out, list) else json.dumps(out, ensure_ascii=False))
    return 0


def refuse_unexpected(kind: type[BaseException], _value: BaseException, _trace: TracebackType | None) -> None:
    print(f"faker-kit: refused: unexpected {kind.__name__}; nothing more was done", file=sys.stderr)


if __name__ == "__main__":
    sys.excepthook = refuse_unexpected
    sys.exit(main())
