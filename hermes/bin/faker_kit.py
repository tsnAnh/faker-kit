#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import hmac
import http.client
import json
import os
import platform
import re
import secrets
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import IO, NoReturn
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
if (HERE.parent / "vendor").is_dir() and str(HERE.parent / "vendor") not in sys.path:
    sys.path.append(str(HERE.parent / "vendor"))

import yaml
from faker_kit_andon import LABEL, MACHINE, Andon, AndonError, Cord, clean_reason, issue_body
from faker_kit_cards import CardLoop, create_unit, unit_rows
from faker_kit_cloud import CHECKOUT, EFFORT_LEVELS, Cloud, CloudError, trust_checkout
from faker_kit_env import CLAUDE_CONFIG_DIR, EnvFileError, parse_env_file, set_env, use_bot_env
from faker_kit_gh import NAME, Coded, Gh, Refused, Runner, credential_problem, credential_state, fix_git_protocol, run_gh
from faker_kit_guard.policy import (
    DEFAULT_HEARTBEAT_EVERY,
    INTERVAL,
    REPO,
    RESERVED_NAMES,
    SHADOWING_BOT_NAMES,
    Bot,
    Policy,
    PolicyError,
    faker_kit_home,
    load_policy,
    parse_policy,
)
from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_host import GITHUB_CODES, Tool, clean, envelope, reconcile_tunnel, run_tool
from faker_kit_import import GITHUB_REPO, ImportRefused
from faker_kit_import import prepare as prepare_import
from faker_kit_import import verify as verify_import
from faker_kit_incident_sources import FetchJson, fetch_json
from faker_kit_incidents import FetchStatus, IncidentStore, Watch, fetch_status
from faker_kit_issues import LABELS, IssueStore, Reports
from faker_kit_kanban import Kanban, KanbanRunner, cutover, hermes_runner
from faker_kit_launch import DISPATCH_FILE, BackendCheck, ClaudeLauncher, ClaudeState, Dispatch, Factory, check_claude_backend, claude_cli_state, pick_factory, preflight, read_dispatch
from faker_kit_loop import DEFAULT_OUTCOME, OUTCOMES, Loop, LoopError, part_row
from faker_kit_machine import WEBHOOK_HOST, WEBHOOK_PORT, Machine, MachineError, Project, check_model_choice, edit_guard, load_machine, route_url, routes, secret_env, stale_hook, stale_route
from faker_kit_merge import MERGE_METHOD, PR_LABEL, PR_LABEL_COLOR, PR_LABEL_DESCRIPTION, Evidence, MergeLock, decide, from_fork, label_refusal, label_state, token, unlabeled_text
from faker_kit_migrate import migrate_chief, migrate_project
from faker_kit_profiles import Profiles
from faker_kit_refresh import clean_source, exclusive, installed_commit, latest_main, recorded_source, refresh
from faker_kit_runner import RunnerError, Services, Slot, build_image, check_slot, up
from faker_kit_t3code import T3CodeLauncher, check_t3code_backend, parse_settings, unit_id
from install_config import InstallError, config_set, hermes, mapping, value_at

BRIEF_START = "/faker-mode"
OWNER_DECISIONS_LINE = re.compile(r"^OWNER DECISIONS:[ \t]*\S", re.M)
MAX_BRIEF_CHARS = 60_000
SESSION_MODEL = re.compile(r"\A(?:opus|sonnet|haiku|fable|claude-[a-z0-9]+(?:-[a-z0-9]+){1,6})\Z")
UNIT_LINE = re.compile(r"\AUnit: ([a-z0-9][a-z0-9-]{0,63})[ \t]*\r?(?:\n|\Z)")
STATUS_PRS = 30
MERGEABLE_READS = 5
MERGEABLE_PAUSE_SECONDS = 5.0
MERGE_WAIT_POLL_SECONDS = 30.0
MERGE_WAIT_MAX_SECONDS = 1800
OPEN_WAIT_DEFAULT_SECONDS = 600
EXIT_REFUSED = 1
EXIT_AMBIGUOUS = 3
MATCH_MAX_CHARS = 100
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
UPDATED_AT = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
ANDON_FOOTER = (
    "\n\nStop rule: before every push, run `gh api 'repos/{repo}/issues?labels=agent:andon&state=open' --jq length`. "
    "If it prints anything but 0, do not push or update a pull request; say so and end the session.\n"
    "\nEffort rule: you and any subagent run Sonnet at medium effort by default, never at effort xhigh or max, whatever a playbook says; high is the highest allowed. Never use Opus for coding.\n"
    "\nLabel rule: when you open the pull request, add the label faker-kit to it in the same step (create the label first if the repository lacks it), "
    "check the label is on the pull request before the session ends, and add the line `Label: faker-kit` to the pull request body. "
    "Never add the label to a pull request you did not open. If you cannot use gh, use the GitHub integration you have.\n"
)
FAKER_FOOTER = (
    "\nFaker-mode rule: Work as faker-mode says: load the faker-mode skill first and follow it. Spawn every subagent you start inside a playbook step "
    "as subagent_type \"faker-agent\" and put the absolute path of the faker-mode skill's SKILL.md in its prompt. "
    "Make the smallest change that meets the goal. "
    "Add no new documentation file unless this brief asks for one. Write no code comment except a non-obvious why. "
    "Run the no-comments pass before the pull request opens. If faker-mode is not installed in this session, "
    "say so in the pull request body and follow these rules anyway. "
    "Do not decide an unspecified requirement or choose between visible patterns (a new switch, screen, flag) yourself: "
    "use the pattern the existing items use; if nothing settles it, leave that part out, finish the rest, "
    "and list the question under \"Open questions\" in the pull request body.\n"
)
FAKER_RULE = FAKER_FOOTER.strip().removeprefix("Faker-mode rule: ")
LOOP_ACTIONS = {"chief": ("show", "set", "digest", "migrate"), "project": ("show", "migrate", "unit")}
ROLE_COMMANDS = {
    "project": {"launch", "merge", "status", "andon", "incidents", "issues", "loop"},
    "bootstrap": {"add-project", "launch", "status", "andon", "refresh", "import"},
    "chief": {"status", "handoff", "loop", "add-project", "merge", "refresh"},
}
OWNER_ONLY = {"install", "runner", "projects", "host"}
SECRET_COMMANDS = {"install", "projects", "host"}
HOST_LOCK = "host-update.lock"
HOST_ACTIONS = ("status", "update", "reload", "rotate")
OWNER_CREDENTIAL_FILE = "host-secret"
SECRET_STDIN_BYTES = 128
OWNER_CREDENTIAL_NEEDED = "this command needs the machine owner's credential"
LISTENER_ENV = "FAKER_KIT_WEBHOOK_LISTENER"
PING_ATTEMPTS = 6
PING_PAUSE_SECONDS = 1.0
PING_TIMEOUT_SECONDS = 5
GATEWAY_RESTART = "hermes -p default gateway restart"
PUBLIC_PATH_REPAIR = "bash hermes/setup-machine.sh --config ~/.hermes/faker-kit/machine.yaml"
Poster = Callable[[str, dict[str, str], bytes], "int | str"]
Refresher = Callable[[Path], str]


def post_ping(url: str, headers: dict[str, str], body: bytes) -> int | str:
    parts = urlsplit(url)
    connect = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = connect(parts.hostname or WEBHOOK_HOST, parts.port, timeout=PING_TIMEOUT_SECONDS)
    try:
        conn.request("POST", parts.path or "/", body=body, headers=headers)
        return conn.getresponse().status
    except (OSError, http.client.HTTPException) as err:
        return f"unreachable ({err.__class__.__name__})"
    finally:
        conn.close()


ANDON_COLOR = "b60205"
ANDON_DESCRIPTION = "The andon cord: agents stop, and nothing new starts until the owner clears it in Hermes"
SCHEDULE_DEFAULTS = {"heartbeat_every": DEFAULT_HEARTBEAT_EVERY}


class UsageError(Exception):
    def __init__(self, message: str, source: argparse.ArgumentParser) -> None:
        super().__init__(message)
        self.source = source


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message, self)


def iso_utc(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


def read_offered_secret(stdin: IO[bytes]) -> bytes:
    try:
        line = stdin.readline(SECRET_STDIN_BYTES + 2)
    except (OSError, ValueError):
        return b""
    line = line.removesuffix(b"\n")
    return line if len(line) <= SECRET_STDIN_BYTES else b""


def current_bot() -> str:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.name if home.parent.name == "profiles" else "default"


def check_name(value: str, what: str) -> None:
    if not NAME.match(value) or value in RESERVED_NAMES:
        raise Refused(f"{what} {value!r} must be lowercase letters, digits, - and _, and not a reserved name ({', '.join(sorted(RESERVED_NAMES))})")


def check_repo(value: str) -> None:
    if not REPO.match(value):
        raise Refused(f"{value!r} is not OWNER/REPO")


def check_interval(value: str | None, what: str) -> None:
    if value is not None and not INTERVAL.match(value):
        raise Refused(f"{what} must be a positive interval like 10m or 1h")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-")[:63]


class WaitExpired(Refused):
    pass


class PutFailed(Refused):
    pass


class Kit:
    def __init__(self, run: Runner = run_gh, cloud: Cloud | None = None, home: Path | None = None, now: Callable[[], float] = time.time,
                 fetch_api: FetchJson = fetch_json, fetch_url: FetchStatus = fetch_status, sleep: Callable[[float], None] = time.sleep, post: Poster = post_ping,
                 tool: Tool = run_tool, refresher: Refresher = refresh, claude_state: ClaudeState = claude_cli_state,
                 kanban_runner: Callable[[str], KanbanRunner] = hermes_runner) -> None:
        self.claude_state = claude_state
        self.kanban_runner = kanban_runner
        self.run, self.now, self.fetch_api, self.fetch_url, self.sleep, self.post = run, now, fetch_api, fetch_url, sleep, post
        self.tool, self.refresher = tool, refresher
        self.home = home or faker_kit_home()
        self.root = self.home.parent
        self.kit = self.home / "kit"
        self.pkg = self.kit / "hermes"
        self.cloud = cloud or Cloud(run_gh=run)
        self.andon = Andon(self.home)
        self.profiles = Profiles(self.root, self.pkg, SECRET_SHAPES, self.home)
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

    def require_credential(self) -> None:
        state, problem = credential_state(self.run)
        if problem:
            raise Coded(problem, GITHUB_CODES[state], "sign_in_github" if state != "unreachable" else None)

    def project_bot(self, policy: Policy, project: str) -> Bot:
        bot = policy.projects().get(project)
        if bot is None:
            raise Refused(f"no project {project}; create it with add-project first")
        return bot

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
        if effort is not None and effort not in EFFORT_LEVELS:
            raise Refused(f"--effort takes one of {', '.join(EFFORT_LEVELS)}")
        if caller is not None and caller.role == "project":
            target = caller
        elif project:
            target = self.project_bot(self.policy(), project)
        else:
            raise Refused("name the project: launch --project P --brief-file F")
        text = self.brief_text(caller.name if caller else "default", brief_file).strip()
        if not text.startswith(BRIEF_START):
            raise Refused(f"the brief must start with {BRIEF_START}, so the session works in faker-mode")
        if not OWNER_DECISIONS_LINE.search(text):
            raise Refused('the brief must list the owner\'s decisions on an OWNER DECISIONS: line; write "none: <why>" when there are none')
        if len(text) > MAX_BRIEF_CHARS:
            raise Refused(f"the brief is longer than {MAX_BRIEF_CHARS} characters; write the detail into a file in the repository and point to it")
        if SECRET_SHAPES.search(text):
            raise Refused("the brief looks like it holds a token or key; a session's first message is a process argument, so no secret goes in it")
        repo = str(target.repo)
        cord = self.cord(target.name)
        if cord:
            raise Refused(f"{cord.describe()}; nothing starts until it is cleared")
        try:
            use_bot_env(self.root / "profiles" / target.name)
        except EnvFileError as err:
            raise Refused(str(err)) from None
        self.require_credential()
        dispatch = self.dispatch(target.name, repo)
        preflight(dispatch, self.backend_checks(brief_file))
        launcher = pick_factory(dispatch, self.backends(caller.name if caller else "default", brief_file))(dispatch)
        prompt = text + ANDON_FOOTER.format(repo=repo)
        if FAKER_RULE not in text:
            prompt += FAKER_FOOTER
        digest = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        owner, name = repo.split("/")
        workdir = self.home / "checkouts" / f"{owner}__{name}" / f"launch-{int(self.now())}-{digest}"
        result = launcher.start(repo, prompt, workdir, model, effort)
        return {"repo": repo, "backend": dispatch.backend, "brief": digest, **result.fields}

    def source_import(self, caller: Bot | None, step: str, project: str, source: str, repo_id: int, receipt: str) -> dict:
        bot = self.project_bot(self.policy(), project)
        try:
            use_bot_env(self.root / "profiles" / bot.name)
        except EnvFileError as err:
            raise Refused(str(err)) from None
        self.require_credential()

        def gh(args: list[str]) -> str:
            try:
                return self.run(args)
            except Refused as err:
                raise ImportRefused("github_error", str(err)) from None

        try:
            if step == "verify":
                return verify_import(gh, self.home, caller.name if caller else "default", project, receipt)
            found = GITHUB_REPO.search(recorded_source(self.home))
            if found is None:
                raise Refused("the recorded kit source is not a GitHub repository, so the Mac cannot fetch the pinned helper from it")
            return prepare_import(gh, self.home, caller.name if caller else "default", project, str(bot.repo), repo_id, source, installed_commit(self.home), found.group(1), self.now())
        except ImportRefused as err:
            raise Refused(f"{err.code}: {err}") from None

    def dispatch(self, bot: str, repo: str) -> Dispatch:
        gh = Gh(self.home, bot, repo, self.run)
        default = gh.default_branch()
        if not default:
            raise Refused(f"cannot read the default branch of {repo}")
        return read_dispatch(gh.file_text(DISPATCH_FILE, default), default)

    def backend_checks(self, brief_file: str) -> dict[str, BackendCheck]:
        return {
            "claude": check_claude_backend(self.claude_state),
            "t3code": lambda dispatch: check_t3code_backend(dispatch, Path(brief_file).name),
        }

    def backends(self, owner: str, brief_file: str) -> dict[str, Factory]:
        return {
            "claude": lambda _dispatch: ClaudeLauncher(self.cloud, self.run),
            "t3code": lambda dispatch: T3CodeLauncher(parse_settings(dispatch.t3code, dispatch.default_branch), self.home, owner, unit_id(Path(brief_file).name), self.now),
        }

    def evidence(self, gh: Gh, pr: int, head: str, default: str, pull: dict) -> Evidence:
        head_info = pull.get("head") or {}
        ref = str(head_info.get("ref") or "")
        owner = str(((head_info.get("repo") or {}).get("owner") or {}).get("login") or str(head_info.get("label") or "").partition(":")[0])
        head_tree = gh.workflows_tree(head)
        return Evidence(gh.check_runs(head), gh.statuses(head), gh.reviews(pr),
                        gh.head_pulls(head), gh.head_runs(head),
                        ref, gh.branch_pulls(owner, ref) if owner and ref else None, gh.timeline(pr), str(pull.get("created_at") or ""),
                        head_tree is not None and head_tree == gh.workflows_tree(default))

    def mergeable_pull(self, gh: Gh, repo: str, pr: int, owner_approved: bool = False) -> tuple[dict, str, str]:
        for read in range(MERGEABLE_READS):
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
                if owner_approved:
                    raise Refused(f"#{pr} conflicts with its base branch, so the owner-requested merge cannot land; the owning session must update the existing branch against the base, "
                                  "resolve the conflict, rerun the checks and push a new head; then the owner asks for the merge again (an approval covers only the message that gave it)")
                raise Refused(f"#{pr} conflicts with its base branch; its author must update it first")
            if data.get("mergeable") is True:
                return data, head, default
            if read < MERGEABLE_READS - 1:
                self.sleep(MERGEABLE_PAUSE_SECONDS)
        raise Refused(f"GitHub is still checking #{pr} for conflicts; run merge again in a minute")

    @staticmethod
    def wait_seconds(text: str | None) -> int:
        if text is None:
            return 0
        if not re.fullmatch(r"[0-9]{1,6}", text) or int(text) > MERGE_WAIT_MAX_SECONDS:
            raise Refused(f"--wait takes a whole number of seconds from 0 to {MERGE_WAIT_MAX_SECONDS}, not {token(text)}")
        return int(text)

    def merge_command(self, caller: Bot | None, pr: int | None, project: str | None, owner_approved: bool, wait: str | None = None,
                      everything: bool = False, all_projects: bool = False, match: str | None = None) -> dict:
        if caller is None or caller.role not in ("project", "chief"):
            raise Refused("merge runs in a project bot's chat or the chief of staff's chat")
        chosen = [flag for flag, on in (("--pr", pr is not None), ("--open", everything), ("--all-projects", all_projects), ("--match", match is not None)) if on]
        if not chosen:
            raise Refused("merge needs a target: --pr <number>, --open, --all-projects, or --match <text>")
        if pr is not None and len(chosen) > 1:
            raise Refused(f"--pr names the pull request and cannot be combined with {' or '.join(c for c in chosen if c != '--pr')}; a number the owner typed is never replaced")
        if len(chosen) > 1 and chosen != ["--open", "--all-projects"]:
            raise Refused(f"{' and '.join(chosen)} cannot be combined; use --open or --all-projects for every pull request, --match for one")
        batch = pr is None and match is None
        seconds = self.wait_seconds(wait if wait is not None or not batch else str(OPEN_WAIT_DEFAULT_SECONDS))
        if pr is None and not owner_approved:
            raise Refused(f"{chosen[0]} merges only when the owner asked for it in their own message: add --owner-approved; a bot never picks its own merge targets")
        if all_projects and caller.role != "chief":
            raise Refused("--all-projects is for the chief of staff; a project bot merges its own project with --open")
        if caller.role == "chief":
            if not all_projects and (not project or not owner_approved):
                raise Refused("the chief of staff merges only when the owner asks: faker-kit merge --project <project> --pr <number> --owner-approved")
            if all_projects and project:
                raise Refused("--all-projects covers every project; drop --project")
            bots = (sorted(self.policy().projects().values(), key=lambda b: b.project or "") if all_projects
                    else [self.project_bot(self.policy(), str(project))])
            approved = True
        else:
            if project and project != caller.project:
                raise Refused(f"{caller.name} merges only its own project's pull requests; the chief of staff merges for another project")
            bots, approved = [caller], owner_approved
        if pr is not None:
            return self.merge_named(bots[0], pr, approved, seconds)
        if match is not None:
            return self.merge_matching(bots[0], match, seconds)
        return self.merge_open(bots, seconds, all_projects)

    def acting_login(self, bot: Bot) -> str:
        try:
            login = str(Gh(self.home, bot.name, str(bot.repo), self.run).json("user").get("login") or "")
        except (Refused, ValueError) as err:
            raise Refused(f"cannot read the GitHub login this machine acts as ({err}); nothing was merged, because a bulk merge touches only that login's pull requests") from err
        if not login:
            raise Refused("GitHub returned no login for this machine; nothing was merged, because a bulk merge touches only that login's pull requests")
        return login

    @staticmethod
    def open_rows(bot: Bot, login: str) -> Callable[[Gh], tuple[list[dict], list[dict], list[dict]]]:
        def read(gh: Gh) -> tuple[list[dict], list[dict], list[dict]]:
            own: list[dict] = []
            drafts: list[dict] = []
            foreign: list[dict] = []
            for pull in gh.open_pulls():
                row = {"project": bot.project, "number": int(pull["number"]), "title": re.sub(r"[\x00-\x1f\x7f]", " ", str(pull.get("title") or ""))[:120],
                       "head": str((pull.get("head") or {}).get("sha") or "")[:12], "branch": str((pull.get("head") or {}).get("ref") or ""),
                       "author": re.sub(r"[^A-Za-z0-9_\[\]-]", "?", str((pull.get("user") or {}).get("login") or "ghost"))[:40]}
                if pull.get("draft"):
                    drafts.append(row)
                elif str(row["author"]).lower() == login.lower():
                    own.append(row)
                else:
                    foreign.append(row)
            return own, drafts, foreign
        return read

    def open_pulls_of(self, bot: Bot, login: str) -> tuple[list[dict], list[dict], list[dict]]:
        return self.open_rows(bot, login)(Gh(self.home, bot.name, str(bot.repo), self.run))

    def merge_matching(self, bot: Bot, text: str, wait: int) -> dict:
        needle = text.strip().lower()
        if not needle or len(needle) > MATCH_MAX_CHARS:
            raise Refused(f"--match takes some text from the pull request's title or branch, up to {MATCH_MAX_CHARS} characters")
        own, _, foreign = self.open_pulls_of(bot, self.acting_login(bot))

        def has(row: dict) -> bool:
            return needle in row["title"].lower() or needle in row["branch"].lower()

        found = [row for row in own if has(row)]
        if not found:
            others = [row for row in foreign if has(row)]
            note = ("; pull requests by other authors match and bulk merge never touches them (only a number the owner names merges one): "
                    + "; ".join(f"#{r['number']} \"{r['title']}\" by {r['author']}" for r in others)) if others else ""
            raise Refused(f"no open pull request of {bot.project} that is not a draft and was opened by this machine's login has {token(text)} in its title or branch{note}")
        if len(found) > 1:
            return {"status": "ambiguous", "candidates": [{"project": r["project"], "number": r["number"], "title": r["title"]} for r in found]}
        return self.merge(bot, found[0]["number"], True, wait)

    def merge_open(self, bots: list[Bot], wait: int, all_projects: bool) -> dict:
        login = self.acting_login(bots[0]) if bots else ""
        resolved: list[tuple[Bot, dict]] = []
        skipped: list[dict] = []
        not_owner: list[dict] = []
        unreadable: list[dict] = []
        for bot in bots:
            try:
                own, drafts, foreign = self.open_pulls_of(bot, login)
            except Refused as err:
                if not all_projects:
                    raise
                unreadable.append({"project": bot.project, "reason": str(err)})
                continue
            resolved.extend((bot, row) for row in own)
            skipped.extend({k: row[k] for k in ("project", "number", "title")} for row in drafts)
            not_owner.extend({k: row[k] for k in ("project", "number", "title", "author")} for row in foreign)
        listing: dict[str, object] = {"resolved": [{k: row[k] for k in ("project", "number", "title", "head")} for _, row in resolved], "skipped_drafts": skipped,
                                      "skipped_not_owner": not_owner}
        if unreadable:
            listing["unreadable"] = unreadable
        print(json.dumps(listing, ensure_ascii=False), flush=True)
        if not resolved:
            raise Refused(f"no open pull request is eligible: a bulk merge takes only non-draft pull requests opened by {login}; nothing was merged")
        started, results, spent = self.now(), [], False
        for bot, row in resolved:
            entry: dict[str, object] = {"project": row["project"], "pr": row["number"]}
            remaining = max(0, wait - int(self.now() - started))
            if wait and (spent or remaining == 0):
                results.append({**entry, "status": "not attempted", "reason": "wait budget used"})
                continue
            try:
                landed = self.merge(bot, row["number"], True, remaining)
                results.append({**entry, "status": "merged", "head": landed["head"], "bypassed": landed.get("bypassed", [])})
            except Refused as err:
                spent = spent or isinstance(err, WaitExpired)
                results.append({**entry, "status": "refused", "reason": str(err)})
        counts = {status: sum(1 for r in results if r["status"] == status) for status in ("merged", "refused", "not attempted")}
        return {"results": results, **counts, "unreadable": len(unreadable), "complete": not (counts["refused"] or counts["not attempted"] or unreadable)}

    def merge_named(self, bot: Bot, pr: int, owner_approved: bool, wait: int) -> dict:
        landed = self.merge(bot, pr, owner_approved, wait)
        try:
            author = str((Gh(self.home, bot.name, str(bot.repo), self.run).pull(pr).get("user") or {}).get("login") or "")
            login = self.acting_login(bot)
        except (Refused, ValueError):
            return landed
        if author and author.lower() != login.lower():
            landed.update(author=author, named_by_owner=True)
        return landed

    def merge(self, bot: Bot, pr: int, owner_approved: bool = False, wait: int = 0) -> dict:
        waited: dict[str, int] = {}
        head, bypassed = self.land(bot, pr, owner_approved, wait, waited)
        landed: dict[str, object] = {"pr": pr, "merged": True, "head": head, "method": MERGE_METHOD}
        if owner_approved:
            landed["owner_approved"] = True
        if bypassed:
            landed["bypassed"] = list(bypassed)
        if wait:
            landed["wait"] = waited
        return landed

    def land(self, bot: Bot, pr: int, owner_approved: bool, wait: int = 0, waited: dict[str, int] | None = None) -> tuple[str, tuple[str, ...]]:
        repo = str(bot.repo)
        gh = Gh(self.home, bot.name, repo, self.run)
        started, polls, checked = self.now(), 0, ""
        while True:
            with MergeLock(self.home, bot.name).locked():
                data, head, default = self.mergeable_pull(gh, repo, pr, owner_approved)
                if checked and head != checked:
                    raise Refused(f"the head of #{pr} moved from {checked[:12]} to {head[:12]} during the wait; nothing was merged, run merge again")
                checked, polls = head, polls + 1
                cord = self.cord(bot.name)
                if cord:
                    clear = ("; this project bot clears it with `faker-kit andon clear` only on the owner's own words, "
                             "in its own chat or in a labelled verbatim chief relay, and never by itself; tell the owner and wait"
                             if owner_approved else "")
                    raise Refused(f"{cord.describe()}; nothing merges until the owner clears it{clear}")
                refusal = label_refusal(data, owner_approved)
                if refusal:
                    raise Refused(f"#{pr} is {refusal}")
                evidence = self.evidence(gh, pr, head, default, data)
                probe = decide(pr, head, evidence, repo, default, False) if wait else None
                elapsed = int(self.now() - started)
                if waited is not None:
                    waited.update(seconds=elapsed, polls=polls)
                if probe is not None and probe.pending:
                    if elapsed >= wait:
                        raise WaitExpired(f"{probe.message}; still pending after waiting {elapsed} seconds over {polls} polls (bound {wait})")
                else:
                    verdict = decide(pr, head, evidence, repo, default, owner_approved)
                    if verdict.action != "merge":
                        raise Refused(verdict.message)
                    return self.put_merge(gh, repo, pr, head), verdict.bypassed
            self.sleep(min(MERGE_WAIT_POLL_SECONDS, wait - elapsed))

    def put_merge(self, gh: Gh, repo: str, pr: int, head: str) -> str:
        try:
            self.run(["api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/merge", "-f", f"merge_method={MERGE_METHOD}", "-f", f"sha={head}"])
        except Refused as err:
            landed = self.landed_after_error(gh, pr, head)
            if landed:
                return head
            raise PutFailed(self.put_failure(pr, landed, err)) from err
        return head

    @staticmethod
    def landed_after_error(gh: Gh, pr: int, head: str) -> bool | None:
        try:
            data = gh.pull(pr)
        except (Refused, ValueError):
            return None
        if data.get("merged_at"):
            return (data.get("head") or {}).get("sha") == head or None
        return False if data.get("state") == "open" else None

    @staticmethod
    def put_failure(pr: int, landed: bool | None, err: Refused) -> str:
        if landed is False:
            return f"the merge request for #{pr} was sent and GitHub refused it ({err}); #{pr} is still open"
        return (f"the merge request for #{pr} was sent and the result could not be confirmed ({err}): #{pr} may or may not have merged; "
                f"check #{pr} on GitHub before merging again")

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
        problem = credential_problem(self.run)
        if problem:
            return [problem]
        policy = self.policy()
        if everything or caller is None or caller.role != "project":
            machine = self.andon.read(MACHINE)
            lines = [f"ANDON for the machine: {machine.reason}. Nothing new starts in any project."] if machine else []
            for bot in sorted(policy.projects().values(), key=lambda b: b.project or ""):
                lines.append(self.project_line(bot))
            return lines or ["no projects"]
        gh = Gh(self.home, caller.name, str(caller.repo), self.run)
        default = gh.default_branch()
        own = [pull for pull in gh.open_pulls() if not from_fork(pull, gh.repo)]
        return [self.pr_line(gh, pull, default) for pull in own[:STATUS_PRS]] or ["no open pull requests"]

    def project_line(self, bot: Bot) -> str:
        cord = self.andon.read(bot.name)
        head = f"{bot.project} ({bot.name}, {bot.repo})" + (f" ANDON: {cord.reason}" if cord else "")
        profile = self.profiles.live(bot.name)
        if profile is None:
            head += f" PROFILE UNKNOWN: could not check whether bot {bot.name} has a Hermes profile; run status again"
        elif not profile:
            head += f" NOT SET UP: bot {bot.name} has no Hermes profile, so this project does nothing yet; the bootstrap bot or chief of staff creates it with one approval in chat"
        try:
            pulls = Gh(self.home, bot.name, str(bot.repo), self.run).open_pulls()
        except Refused as err:
            return f"{head}: unreadable: {err}"
        drafts = sum(1 for p in pulls if p.get("draft"))
        return f"{head}: open {len(pulls)} (draft {drafts}, ready {len(pulls) - drafts})"

    def pr_line(self, gh: Gh, pull: dict, default: str) -> str:
        number = int(pull["number"])
        detail = gh.pull(number)
        head = str((detail.get("head") or {}).get("sha") or "")
        who = re.sub(r"[^A-Za-z0-9_\[\]-]", "?", str((pull.get("user") or {}).get("login") or "ghost"))[:40]
        verdict = decide(number, head, self.evidence(gh, number, head, default, detail), gh.repo, default)
        gate = {"merge": "ready to merge", "refuse": f"blocked: {verdict.message}"}[verdict.action]
        labels = label_state(detail)
        if labels != "labeled":
            gate = f"blocked: {unlabeled_text(labels)}"
        mergeable = {True: "mergeable", False: "conflicts", None: "mergeable unknown"}[detail.get("mergeable")]
        unit = UNIT_LINE.match(str(detail.get("body") or ""))
        updated = str(pull.get("updated_at") or "")
        return " | ".join([f"#{number}", *([f"unit {unit.group(1)}"] if unit else []), f"by {who}", "draft" if pull.get("draft") else "ready",
                           "labeled" if labels == "labeled" else "unlabeled", mergeable,
                           f"updated {updated if UPDATED_AT.match(updated) else 'unknown'}", f"head {head if SHA.match(head) else 'unknown'}", gate])

    def kanban(self, caller: Bot) -> Kanban:
        return Kanban(self.kanban_runner(caller.name))

    def handoff(self, caller: Bot, project: str, part: str, decisions: str, outcome: str = DEFAULT_OUTCOME) -> dict:
        owner = self.policy().projects().get(project)
        if owner is None:
            raise Refused(f"no project {project} on this machine; faker-kit status --all lists them")
        if cutover(self.home):
            return CardLoop(self.kanban(caller), self.home, caller.name).add(project, owner.name, str(owner.repo), part, decisions, self.now(), outcome)
        return part_row(Loop(self.home, caller.name).add(project, owner.name, part, decisions, self.now(), outcome))

    def loop(self, caller: Bot, action: str, part_id: int | str | None, state: str | None, prs: list[int], waits_on: str | None, reported: bool,
             everything: bool, evidence: list[str] | None = None, dry_run: bool = False, apply: bool = False, part: int | None = None) -> dict:
        if action not in LOOP_ACTIONS[caller.role]:
            raise Refused(f"loop {action} is not one of {caller.name}'s actions ({', '.join(LOOP_ACTIONS[caller.role])})")
        if action == "migrate":
            return self.loop_migrate(caller, part_id, state, prs, waits_on, reported, evidence, dry_run, apply, part)
        if dry_run or apply:
            raise Refused("--dry-run and --apply belong to loop migrate")
        if action == "unit":
            return self.loop_unit(caller, part_id, state, waits_on, reported, evidence, prs, part)
        if part is not None:
            raise Refused("--part belongs to loop unit")
        if caller.role == "project":
            if part_id is not None or state or prs or waits_on or reported or evidence:
                raise Refused("loop show takes no part id or changes")
            if not cutover(self.home):
                raise Refused("the Kanban cutover is not done: units still live in units.tsv, and loop migrate --dry-run shows what moving them would do")
            return unit_rows(self.kanban(caller), str(caller.project))
        if not cutover(self.home):
            return self.old_loop(caller, action, part_id, state, prs, waits_on, reported, everything, evidence)
        cards = CardLoop(self.kanban(caller), self.home, caller.name)
        if action == "set":
            if part_id is None:
                raise Refused("loop set takes the part's id: faker-kit loop set <id> --state <state>")
            return cards.update(self.part_number(part_id), state, prs, waits_on, reported, self.now(), evidence)
        if part_id is not None or state or prs or waits_on or reported or evidence:
            raise Refused(f"loop {action} takes no part id or changes; use loop set <id>")
        return cards.digest(self.now()) if action == "digest" else cards.show(everything)

    @staticmethod
    def part_number(value: int | str) -> int:
        if isinstance(value, int):
            return value
        raise Refused("a part id is a number")

    def old_loop(self, caller: Bot, action: str, part_id: int | str | None, state: str | None, prs: list[int], waits_on: str | None, reported: bool,
                 everything: bool, evidence: list[str] | None) -> dict:
        loop = Loop(self.home, caller.name)
        if action == "set":
            if part_id is None:
                raise Refused("loop set takes the part's id: faker-kit loop set <id> --state <state>")
            return part_row(loop.update(self.part_number(part_id), state, prs, waits_on, reported, self.now(), evidence))
        if part_id is not None or state or prs or waits_on or reported or evidence:
            raise Refused(f"loop {action} takes no part id or changes; use loop set <id>")
        return loop.digest(self.now()) if action == "digest" else loop.show(everything)

    def loop_migrate(self, caller: Bot, part_id: int | str | None, state: str | None, prs: list[int], waits_on: str | None, reported: bool,
                     evidence: list[str] | None, dry_run: bool, apply: bool, part: int | None) -> dict:
        if part_id is not None or state or prs or waits_on or reported or evidence or part is not None:
            raise Refused("loop migrate takes only --dry-run or --apply")
        if dry_run == apply:
            raise Refused("loop migrate takes exactly one of --dry-run (read only) or --apply")
        kanban = self.kanban(caller)
        if caller.role == "chief":
            repos = {name: str(bot.repo) for name, bot in self.policy().projects().items()}
            return migrate_chief(kanban, self.home, caller.name, repos, apply, self.now())
        return migrate_project(kanban, self.home, caller.name, str(caller.project), str(caller.repo), apply, self.now())

    def loop_unit(self, caller: Bot, unit: int | str | None, state: str | None, waits_on: str | None, reported: bool, evidence: list[str] | None,
                  prs: list[int], part: int | None) -> dict:
        if not isinstance(unit, str) or state or waits_on or reported or evidence:
            raise Refused("loop unit takes the unit id: faker-kit loop unit <unit-id> [--part N] [--pr N]")
        if not cutover(self.home):
            raise Refused("the Kanban cutover is not done: units still live in units.tsv, so no unit card was created")
        return create_unit(self.kanban(caller), caller.name, str(caller.project), str(caller.repo), unit, part, prs, self.now())

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

    def sync_bot(self, bot: Bot, ask_owner: bool, model: str | None, config_dir: str | None, provider: str | None = None) -> dict:
        home = self.profiles.refresh(bot.name, bot.role, ask_owner, model, config_dir, provider)
        out: dict = {"bot": bot.name, "role": bot.role}
        (self.home / "briefs" / bot.name).mkdir(parents=True, exist_ok=True)
        if bot.role == "project":
            if config_dir is None:
                self.profiles.record_config_dir(home, os.environ.get("CLAUDE_CONFIG_DIR", ""))
            intervals = {key: getattr(bot, key) for key in SCHEDULE_DEFAULTS}
            active = self.profiles.github_login(home, self.machine_login())
            changes = self.profiles.ensure_jobs(bot.name, "project-jobs.md", intervals, bot.slack_channel, active)
            out["jobs_created"], out["jobs_removed"] = changes.created, changes.removed
            out["jobs_active"] = active
        if bot.role == "chief":
            active = self.profiles.github_login(home, self.machine_login())
            changes = self.profiles.ensure_jobs(bot.name, "chief-jobs.md", {}, None, active)
            out["jobs_created"], out["jobs_removed"] = changes.created, changes.removed
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

    def ensure_labels(self, repo: str) -> None:
        definitions = {LABEL: (ANDON_COLOR, ANDON_DESCRIPTION), PR_LABEL: (PR_LABEL_COLOR, PR_LABEL_DESCRIPTION), **LABELS}
        for label, (color, description) in definitions.items():
            self.run(["label", "create", label, "-R", repo, "--color", color, "--description", description, "--force"])

    def sync_labels(self, bot: Bot) -> str:
        try:
            self.ensure_labels(str(bot.repo))
        except Refused as err:
            return f"unavailable: {' '.join(str(err).split())[:200]}"
        return "ready"

    def add_project(self, repo: str, bot: str | None = None, project: str | None = None, model: str | None = None,
                    intervals: dict[str, str | None] | None = None, subscription: bool | None = None, slack_channel: str | None = None,
                    refresh_webhooks: bool = True, provider: str | None = None, check: bool = False, reload_gateway: bool = False) -> dict:
        check_repo(repo)
        slug = project or slugify(repo.split("/")[1])
        name = bot or slug
        check_name(name, "bot")
        check_name(slug, "project")
        if name in SHADOWING_BOT_NAMES and name not in self.bots_file()["bots"]:
            raise Refused(f"bot {name!r} is a reserved name: its Hermes alias would shadow the {name} tool; name the bot with --bot")
        wanted = intervals or {}
        for key, value in wanted.items():
            check_interval(value, key)
        check_model_choice(provider, model, bool(subscription), "add-project --provider")
        inherited = subscription if subscription is not None else any(self.profiles.subscription_in_use(b) for b in self.bots_file()["bots"])
        use_subscription = not provider and inherited
        config_dir = self.profiles.claude_login() if use_subscription else None
        proposed = copy.deepcopy(self.bots_file())
        self.register_project(proposed, repo, name, slug, {key: wanted.get(key) for key in SCHEDULE_DEFAULTS}, slack_channel)
        checked_policy = self.policy_from(proposed)
        self.require_credential()
        self.check_repo_readable(repo)
        self.profiles.check_skill_dirs(name)
        if check:
            profile = self.profiles.live(name)
            return {"check": True, "bot": name, "project": slug, "repo": repo, "profile": {True: "exists", False: "missing", None: "unknown"}[profile], "ask_owner": checked_policy.ask_owner}
        fixed = fix_git_protocol(self.run)
        self.profiles.ensure(name)
        data = self.bots_file()
        self.register_project(data, repo, name, slug, {key: wanted.get(key) for key in SCHEDULE_DEFAULTS}, slack_channel)
        policy = self.policy_from(data)
        project_bot = policy.bot(name)
        self.ensure_labels(repo)
        self.write_bots(data)
        out = self.sync_bot(project_bot, policy.ask_owner, model, config_dir, provider)
        out["labels"] = "ready"
        owner, repo_name = repo.split("/")
        try:
            checkout = self.cloud.checkout(repo, self.home / "checkouts" / f"{owner}__{repo_name}" / CHECKOUT)
        except CloudError as err:
            raise Refused(f"cannot prepare the launch checkout: {err}") from err
        kept = self.home / "machine.yaml"
        webhook_url = load_machine(kept).webhook_url if refresh_webhooks and kept.exists() else None
        failed: list[str] = []
        unreachable: list[str] = []
        if webhook_url:
            out["webhooks"] = self.webhooks(webhook_url, [(name, repo)])
            problem = self.restart_gateway() if reload_gateway else None
            failed, unreachable = self.ping_routes(name, out["webhooks"], problem)
        trust_checkout(checkout, os.environ.get(CLAUDE_CONFIG_DIR, ""))
        steps: list[str] = []
        if webhook_url:
            out["webhooks_live"] = not failed
            out["public_path_live"] = not failed and not unreachable
            if unreachable:
                owner_step = f"`{PUBLIC_PATH_REPAIR}`"
                steps.append(f"The public webhook path is not live: {'; '.join(unreachable)} while the local listener answered. "
                             f"The owner runs {owner_step} in the kit checkout to rewrite the tunnel config and restart faker-kit-tunnel, then faker-kit add-project {repo} again to prove it")
            if failed:
                steps.append(f"The webhook routes are not live: {'; '.join(failed)}. The owner runs `{GATEWAY_RESTART}` in their own shell, then faker-kit add-project {repo} again to prove them")
        if not out["jobs_active"]:
            again = f"{repo} --provider {provider} --model {model}" if provider else repo
            steps.append(f"The owner signs in to GitHub for this machine in the Pal app, then runs faker-kit add-project {again} again to start the jobs")
        return {**out, "project": slug, "repo": repo, "launch_checkout": str(checkout), "next": steps, **({"git_protocol_fixed": fixed} if fixed else {})}

    def restart_gateway(self) -> str | None:
        try:
            hermes("-p", "default", "gateway", "restart", timeout=120)
        except InstallError as err:
            return f"the gateway restart failed ({err})"
        return None

    def signed_ping(self, url: str, secret: str, attempts: int = PING_ATTEMPTS) -> int | str:
        body = json.dumps({"zen": "faker-kit ping"}).encode()
        headers = {"Content-Type": "application/json", "X-GitHub-Event": "ping",
                   "X-Hub-Signature-256": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}
        answer: int | str = "no answer"
        for attempt in range(attempts):
            answer = self.post(url, headers, body)
            if isinstance(answer, int) and 200 <= answer < 300:
                break
            if attempt + 1 < attempts:
                self.sleep(PING_PAUSE_SECONDS)
        return answer

    def ping_routes(self, bot: str, hooks: list[dict], problem: str | None, public: bool = True) -> tuple[list[str], list[str]]:
        secret = parse_env_file(self.root / ".env").get(secret_env(bot), "")
        listener = os.environ.get(LISTENER_ENV) or f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}"
        failed, unreachable = [], []
        for hook in hooks:
            path = urlsplit(hook["url"]).path
            route = path.rsplit("/", 1)[-1]
            answer = self.signed_ping(listener.rstrip("/") + path, secret)
            ok = isinstance(answer, int) and 200 <= answer < 300
            hook["ping"] = "ok" if ok else str(answer)
            if not ok:
                failed.append(f"{route} answered {answer}" + (f" and {problem}" if problem else ""))
                continue
            if not public:
                continue
            answer = self.signed_ping(hook["url"], secret)
            hook["public_ping"] = "ok" if isinstance(answer, int) and 200 <= answer < 300 else str(answer)
            if hook["public_ping"] != "ok":
                unreachable.append(f"{route} answered {answer}")
        return failed, unreachable

    def secrets_known(self) -> tuple[str, ...]:
        found: tuple[str, ...] = ()
        with contextlib.suppress(OSError, EnvFileError):
            found = tuple(v for v in parse_env_file(self.root / ".env").values() if v)
        owner = self.host_secret()
        return (*found, owner.decode("utf-8", "replace")) if owner else found

    def host_secret_path(self) -> Path:
        return self.home / OWNER_CREDENTIAL_FILE

    def host_secret(self) -> bytes | None:
        try:
            return self.host_secret_path().read_bytes().removesuffix(b"\n")
        except FileNotFoundError:
            return None
        except OSError:
            return b""

    def write_host_secret(self) -> str:
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.home.chmod(0o700)
        path = self.host_secret_path()
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        stamp = self.now()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(secrets.token_urlsafe(32).encode() + b"\n")
                out.flush()
                os.fsync(out.fileno())
            os.utime(tmp, (stamp, stamp))
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return iso_utc(stamp)

    def mint_host_secret(self) -> bool:
        if self.host_secret_path().exists():
            return False
        self.write_host_secret()
        return True

    def host_credential(self) -> dict:
        try:
            stamp = self.host_secret_path().stat().st_mtime
        except OSError:
            return {"present": False, "created_at": None}
        return {"present": True, "created_at": iso_utc(stamp)}

    def authorize_owner(self, who: str, offered: bool, stdin: IO[bytes]) -> bool:
        known = self.host_secret()
        if known is None:
            if who != "default":
                raise Refused("this command runs only from the owner's own shell, never from a bot")
            return False
        given = read_offered_secret(stdin) if offered else b""
        if not (known and given and hmac.compare_digest(given, known)):
            raise Refused(OWNER_CREDENTIAL_NEEDED)
        return True

    def host_rotate(self) -> dict:
        if self.host_secret() is None:
            raise Refused("there is no owner credential to rotate yet; run host update first")
        with exclusive(self.home, HOST_LOCK):
            created_at = self.write_host_secret()
        return envelope("ok", "The owner credential was replaced.", {"rotated": True, "created_at": created_at})

    def listener_base(self) -> str:
        return (os.environ.get(LISTENER_ENV) or f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}").rstrip("/")

    def host_machine(self) -> Machine:
        try:
            return load_machine(self.home / "machine.yaml")
        except MachineError as err:
            raise Coded(f"This machine's saved configuration cannot be used ({clean(str(err), self.secrets_known())}).", "machine_config_invalid", "contact_support") from None

    def gateway_answers(self) -> bool:
        return isinstance(self.post(self.listener_base() + "/health", {}, b""), int)

    def probe_project(self, machine: Machine, project: Project, secret: str, gateway_up: bool) -> dict:
        row: dict = {"project": project.project, "bot": project.bot, "repo": project.repo, "routes_loaded": False, "public_path_live": None, "reason": None}
        route = next(iter(routes(project.bot)))
        if not secret:
            row["reason"] = "No webhook secret is saved for this project yet."
        elif not gateway_up:
            row["reason"] = "The gateway is not answering."
        else:
            local = self.signed_ping(f"{self.listener_base()}/p/{project.bot}/webhooks/{route}", secret, 1)
            row["routes_loaded"] = isinstance(local, int) and 200 <= local < 300
            if not row["routes_loaded"]:
                row["reason"] = "The gateway does not know this project's routes yet."
            elif machine.webhook_url:
                public = self.signed_ping(route_url(machine.webhook_url, project.bot, route), secret, 1)
                row["public_path_live"] = isinstance(public, int) and 200 <= public < 300
                row["reason"] = None if row["public_path_live"] else f"The public address answered {public}."
            else:
                row["reason"] = "No public address is set for this machine."
        return row

    def host_status(self) -> dict:
        machine = self.host_machine()
        env = parse_env_file(self.root / ".env")
        issues: list[tuple[str, str, str | None]] = []
        state, _ = credential_state(self.run)
        if state != "signed_in":
            wording = {"not_signed_in": "GitHub is not signed in for this machine.", "sign_in_expired": "The GitHub sign-in for this machine expired.",
                       "unreachable": "GitHub could not be reached from this machine."}
            issues.append((GITHUB_CODES[state], wording[state], "sign_in_github" if state != "unreachable" else None))
        kit: dict = {"installed_commit": None, "latest_main_commit": None, "behind": None, "source": None}
        try:
            source = recorded_source(self.home)
            kit["source"] = clean_source(source)
            try:
                kit["installed_commit"] = installed_commit(self.home)
            except Coded:
                issues.append(("kit_install_failed", "The installed kit could not be read.", "contact_support"))
            try:
                kit["latest_main_commit"] = latest_main(source)
            except Coded:
                issues.append(("kit_fetch_failed", "The newest kit could not be looked up.", None))
            if kit["installed_commit"] and kit["latest_main_commit"]:
                kit["behind"] = kit["installed_commit"] != kit["latest_main_commit"]
                if kit["behind"]:
                    issues.append(("kit_behind", "A newer kit is available for this machine.", "update_kit"))
        except Coded:
            issues.append(("kit_source_missing", "No kit source is recorded on this machine.", "contact_support"))
        up = self.gateway_answers()
        if not up:
            issues.append(("gateway_unreachable", "The Hermes gateway is not answering.", "restart_gateway"))
        rows = [self.probe_project(machine, p, env.get(secret_env(p.bot), ""), up) for p in machine.projects]
        for row in rows:
            if up and not row["routes_loaded"]:
                issues.append(("routes_not_loaded", f"The gateway has not loaded the routes for {row['project']}.", "reload_routes"))
            elif row["public_path_live"] is False:
                issues.append(("public_path_down", f"The public address for {row['project']} is not answering.", "reconnect_public_path"))
        data = {"kit": kit, "gateway": {"reachable": up, "port": urlsplit(self.listener_base()).port}, "github": {"state": state}, "projects": rows,
                "owner_credential": self.host_credential(),
                "issues": [{"code": code, "message": message} for code, message, _ in issues]}
        first = next((i for i in issues if i[2]), issues[0] if issues else None)
        return envelope("ok", first[1] if first else "This machine is up to date and reachable.", data, first[2] if first else None)

    def reload_routes(self, machine: Machine) -> tuple[dict, Coded | None]:
        assert machine.webhook_url
        hooks = self.webhooks(machine.webhook_url, [(p.bot, p.repo) for p in machine.projects], github=False)
        problem = self.restart_gateway()
        rows: list[dict] = []
        gateway_down = problem is not None
        for p in machine.projects:
            mine = [h for h in hooks if h["repo"] == p.repo]
            failed, _ = self.ping_routes(p.bot, mine, problem, public=False)
            pings = [str(h["ping"]) for h in mine]
            gateway_down = gateway_down or any(ping.startswith("unreachable") for ping in pings)
            rows.append({"project": p.project, "bot": p.bot, "routes_loaded": not failed, "local_ping": next((ping for ping in pings if ping != "ok"), "ok")})
        data = {"projects": rows, "restarted_gateway": True}
        if gateway_down:
            return data, Coded("The Hermes gateway did not come back after the restart.", "gateway_unreachable", "restart_gateway", data)
        if not all(r["routes_loaded"] for r in rows):
            return data, Coded("The gateway still has not loaded every project's routes.", "routes_not_loaded", "restart_gateway", data)
        return data, None

    def host_reload(self) -> dict:
        machine = self.host_machine()
        if not machine.webhook_url:
            return envelope("ok", "This machine has no public address configured, so there are no webhook routes to load.", {"projects": [], "restarted_gateway": False})
        data, failure = self.reload_routes(machine)
        if failure:
            raise failure
        return envelope("ok", "Restarted the gateway, the only way it loads routes, and every project's routes now answer.", data)

    def reconcile_public_path(self, machine: Machine, secrets: dict[str, str]) -> Coded | None:
        down = []
        for p in machine.projects:
            route = next(iter(routes(p.bot)))
            answer = self.signed_ping(route_url(str(machine.webhook_url), p.bot, route), secrets.get(secret_env(p.bot), ""))
            if not (isinstance(answer, int) and 200 <= answer < 300):
                down.append(p.project)
        if down:
            return Coded(f"The public address is not answering for {', '.join(down)}.", "public_path_down", "reconnect_public_path")
        return None

    def host_update(self) -> dict:
        steps: list[dict] = []
        data: dict = {"from_commit": None, "to_commit": None, "changed": False, "steps": steps}
        first: list[Coded] = []

        def record(name: str, failure: Coded | None, detail: str) -> None:
            steps.append({"name": name, "ok": failure is None, "detail": clean(detail, known_secrets)})
            if failure and not first:
                first.append(failure)

        with exclusive(self.home, HOST_LOCK):
            data["owner_credential"] = {"created": self.mint_host_secret()}
            known_secrets = self.secrets_known()
            try:
                data["from_commit"] = installed_commit(self.home)
                line = self.refresher(self.home)
                data["to_commit"] = installed_commit(self.home)
            except Coded as err:
                record("kit", err, str(err))
                raise Coded(clean(str(err), known_secrets), err.code, {"kit_source_missing": "contact_support", "kit_install_failed": "contact_support"}.get(err.code), data) from None
            data["changed"] = data["from_commit"] != data["to_commit"]
            record("kit", None, line)
            try:
                machine = self.host_machine()
            except Coded as err:
                record("machine", err, str(err))
                raise Coded(str(err), err.code, err.action, data) from None
            if not machine.webhook_url:
                record("reload_routes", None, "No public address is set for this machine, so routes are not managed here.")
            else:
                _, failure = self.reload_routes(machine)
                record("reload_routes", failure, str(failure) if failure else "The gateway was restarted and every project's routes answer.")
                try:
                    _, detail = reconcile_tunnel(Path(machine.tunnel_config) if machine.tunnel_config else None, machine.tunnel_unit, self.now(), self.tool)
                    record("public_tunnel", None, detail)
                except Coded as err:
                    record("public_tunnel", err, str(err))
                if not any(s["name"] == "reload_routes" and not s["ok"] for s in steps):
                    record("public_path", self.reconcile_public_path(machine, parse_env_file(self.root / ".env")), "The public address answers for every project.")
        if first:
            raise Coded(clean(str(first[0]), known_secrets), first[0].code, first[0].action, data)
        moved = f"The kit moved to {data['to_commit'][:12]} and the machine was reconciled." if data["changed"] else "The kit is already current and the machine was reconciled."
        return envelope("ok", moved, data)

    def add_project_envelope(self, out: dict) -> dict:
        data = {key: value for key, value in out.items() if key != "next"}
        if out.get("webhooks_live") is False:
            return envelope("routes_not_loaded", "The gateway has not loaded this project's routes.", data, "reload_routes")
        if out.get("public_path_live") is False:
            return envelope("public_path_down", "The public address for this project is not answering.", data, "reconnect_public_path")
        if out.get("jobs_active") is False:
            return envelope("github_not_signed_in", "The project is set up, but its jobs start once GitHub is signed in for this machine.", data, "sign_in_github")
        return envelope("ok", "Project checked." if out.get("check") else "Project is set up and its routes answer.", data)

    def check_repo_readable(self, repo: str) -> None:
        try:
            answer = self.run(["api", f"repos/{repo}", "--jq", "{permissions}"])
        except Refused as err:
            raise Refused(f"{repo} is not readable on GitHub with this machine's gh login ({err})") from err
        try:
            permissions = json.loads(answer).get("permissions")
        except (ValueError, AttributeError):
            permissions = None
        if not isinstance(permissions, dict) or permissions.get("push") is not True:
            raise Refused(f"this machine's gh login cannot push to {repo}, so the project's labels and pull requests could not be made; sign gh in as an account with write access")

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

    def project_names(self) -> list[str]:
        return sorted(self.policy().projects())

    def install(self, config: Path | None, bootstrap: str | None, model: str | None, subscription: bool, provider: str | None = None) -> dict:
        self.mint_host_secret()
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
            intervals = {"heartbeat_every": item.heartbeat_every}
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
                synced = self.sync_bot(bot, policy.ask_owner, bot_model, config_dir, bot_provider)
                if bot.role == "project":
                    synced["labels"] = self.sync_labels(bot)
                refreshed.append(synced)
        skills = self.profiles.kit_skills(["default", *policy.bots], self.kit / "plugins", wanted) if machine else []
        hooks = self.webhooks(machine.webhook_url, [(p.bot, p.repo) for p in machine.projects]) if machine and machine.webhook_url else []
        if config:
            self.keep_machine(config)
        self.profiles.register_update_step()
        return {"projects": projects, "refreshed": refreshed, "skills": skills, "webhooks": hooks}

    def webhooks(self, base_url: str, projects: list[tuple[str, str]], github: bool = True) -> list[dict]:
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
                done.append({"repo": repo, "url": url, "hook": self.github_hook(repo, url, route["events"], secret) if github else None})
            self.prune_routes(bot, listener)
            stale = json.loads(self.run(["api", f"repos/{repo}/hooks?per_page=100"]) or "[]") if github else []
            for hook in stale:
                url = str((hook.get("config") or {}).get("url") or "")
                if stale_hook(url, base_url, bot):
                    self.run(["api", "-X", "DELETE", f"repos/{repo}/hooks/{int(hook['id'])}"])
                    print(f"faker-kit: pruned GitHub hook {url} on {repo}", file=sys.stderr)
        return done

    def prune_routes(self, bot: str, listener: tuple[str, str]) -> None:
        current = value_at(mapping(self.root / "config.yaml"), (*listener, "extra", "routes"))
        for name in sorted(current if isinstance(current, dict) else {}):
            if stale_route(str(name), bot):
                hermes("-p", "default", "config", "unset", ".".join((*listener, "extra", "routes", str(name))))
                print(f"faker-kit: pruned webhook route {name}", file=sys.stderr)

    def github_hook(self, repo: str, url: str, events: list[str], secret: str) -> str:
        hooks = json.loads(self.run(["api", f"repos/{repo}/hooks?per_page=100"]) or "[]")
        current = next((h for h in hooks if (h.get("config") or {}).get("url") == url), None)
        body = {"active": True, "events": events, "config": {"url": url, "content_type": "json", "insecure_ssl": "0", "secret": secret}}
        if current is None:
            self.run(["api", "-X", "POST", f"repos/{repo}/hooks", "--input", "-"], input=json.dumps({"name": "web", **body}))
            return "created"
        self.run(["api", "-X", "PATCH", f"repos/{repo}/hooks/{int(current['id'])}", "--input", "-"], input=json.dumps(body))
        return "updated"


def secret_flag(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--secret-stdin", action="store_true")


def part_or_unit(text: str) -> int | str:
    return int(text) if text.isdecimal() else text


def parser() -> argparse.ArgumentParser:
    top = Parser(prog="faker-kit")
    sub = top.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("install")
    cmd.add_argument("--config", type=Path)
    cmd.add_argument("--bootstrap-bot")
    cmd.add_argument("--model")
    cmd.add_argument("--provider")
    cmd.add_argument("--claude-subscription", action="store_true")
    secret_flag(cmd)
    cmd = sub.add_parser("refresh")
    cmd = sub.add_parser("host")
    cmd.add_argument("action", choices=HOST_ACTIONS)
    cmd.add_argument("--json", action="store_true")
    secret_flag(cmd)
    cmd = sub.add_parser("projects")
    secret_flag(cmd)
    cmd = sub.add_parser("add-project")
    cmd.add_argument("repo")
    cmd.add_argument("--bot")
    cmd.add_argument("--project")
    cmd.add_argument("--model")
    cmd.add_argument("--provider")
    cmd.add_argument("--claude-subscription", action="store_true", default=None)
    cmd.add_argument("--heartbeat-every")
    cmd.add_argument("--check", action="store_true")
    cmd.add_argument("--json", action="store_true")
    cmd = sub.add_parser("status")
    cmd.add_argument("--all", action="store_true")
    cmd = sub.add_parser("launch")
    cmd.add_argument("--brief-file", required=True)
    cmd.add_argument("--project")
    cmd.add_argument("--model")
    cmd.add_argument("--effort")
    cmd = sub.add_parser("merge", allow_abbrev=False)
    cmd.add_argument("--pr", type=int)
    cmd.add_argument("--project")
    cmd.add_argument("--owner-approved", action="store_true")
    cmd.add_argument("--wait", help=f"seconds to wait for pending checks, 0 to 1800 (default 0; {OPEN_WAIT_DEFAULT_SECONDS} with --open or --all-projects, shared by all of its pull requests)")
    cmd.add_argument("--open", dest="everything", action="store_true", help="every open pull request of the project that is not a draft")
    cmd.add_argument("--all-projects", action="store_true", help="chief only: --open for every project on this machine")
    cmd.add_argument("--match", help="the one open pull request whose title or branch has this text; several matches print ambiguous and exit 3")
    cmd = sub.add_parser("import")
    steps = cmd.add_subparsers(dest="step", required=True)
    step = steps.add_parser("prepare")
    step.add_argument("--project", required=True)
    step.add_argument("--source", required=True)
    step.add_argument("--repo-id", required=True, type=int)
    step = steps.add_parser("verify")
    step.add_argument("--project", required=True)
    step.add_argument("--receipt", required=True)
    cmd = sub.add_parser("issues")
    cmd.add_argument("--claim", type=int)
    cmd = sub.add_parser("incidents")
    cmd.add_argument("--ack")
    cmd = sub.add_parser("handoff")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--part", required=True)
    cmd.add_argument("--decisions", required=True, help='the requirements the owner decided, in their words; "none: <why>" for a bug fix whose expected behaviour is obvious')
    cmd.add_argument("--outcome", choices=OUTCOMES, default=DEFAULT_OUTCOME, help="what done means: a merged pull request (default), or a built, installed, or verified result with evidence")
    cmd = sub.add_parser("loop")
    cmd.add_argument("action", nargs="?", choices=["show", "set", "digest", "migrate", "unit"], default="show")
    cmd.add_argument("id", nargs="?", type=part_or_unit)
    cmd.add_argument("--dry-run", action="store_true", help="loop migrate: read the loop and units and print what moving them to Kanban cards would do; writes nothing")
    cmd.add_argument("--apply", action="store_true", help="loop migrate: create the cards, then the cutover marker")
    cmd.add_argument("--part", type=int, help="loop unit: the part this unit serves")
    cmd.add_argument("--state")
    cmd.add_argument("--pr", type=int, action="append", default=[])
    cmd.add_argument("--waits-on")
    cmd.add_argument("--reported", action="store_true")
    cmd.add_argument("--evidence", action="append", default=[], metavar="STAGE=REF", help="loop set: a one-line reference proving STAGE (built, installed, or verified); repeatable")
    cmd.add_argument("--all", action="store_true")
    cmd = sub.add_parser("andon")
    cmd.add_argument("action", nargs="?", choices=["show", "raise", "clear"], default="show")
    cmd.add_argument("reason", nargs="*")
    cmd = sub.add_parser("runner")
    steps = cmd.add_subparsers(dest="step", required=True)
    secret_flag(steps.add_parser("build"))
    sub_up = steps.add_parser("up")
    sub_up.add_argument("--config", required=True, type=Path)
    secret_flag(sub_up)
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


def run_command(args: argparse.Namespace, kit: Kit, who: str, stdin: IO[bytes]) -> object:
    if args.command in SECRET_COMMANDS or (args.command == "runner" and args.step != "slot"):
        if kit.authorize_owner(who, args.secret_stdin, stdin):
            who = "default"
    elif args.command in OWNER_ONLY and who != "default":
        raise Refused(f"{args.command} runs only from the owner's own shell, never from a bot")
    caller = None if who == "default" else kit.policy().bot(who)
    if caller is not None and args.command not in ROLE_COMMANDS[caller.role]:
        raise Refused(f"{caller.name} is a {caller.role} bot; faker-kit {args.command} is not one of its commands")
    if args.command == "add-project" and caller is not None and caller.role == "chief" and not args.check:
        raise Refused(f"{caller.name} is a chief bot; it may run faker-kit add-project only with --check, and the bootstrap bot sets the project up")
    if args.command == "status":
        return kit.status(caller, args.all)
    if args.command == "install":
        return kit.install(args.config, args.bootstrap_bot, args.model, args.claude_subscription, args.provider)
    if args.command == "refresh":
        return [refresh(kit.home)]
    if args.command == "projects":
        return kit.project_names()
    if args.command == "host":
        if not args.json:
            raise Refused("host commands print JSON for the Pal app; add --json")
        return {"status": kit.host_status, "update": kit.host_update, "reload": kit.host_reload, "rotate": kit.host_rotate}[args.action]()
    if args.command == "add-project":
        intervals = {key: getattr(args, key) for key in SCHEDULE_DEFAULTS}
        added = kit.add_project(args.repo, args.bot, args.project, args.model, intervals, args.claude_subscription or None, provider=args.provider, check=args.check, reload_gateway=caller is None)
        return kit.add_project_envelope(added) if args.json else added
    if args.command == "launch":
        return kit.launch(caller, args.project, args.brief_file, args.model, args.effort)
    if args.command == "merge":
        return kit.merge_command(caller, args.pr, args.project, args.owner_approved, args.wait, args.everything, args.all_projects, args.match)
    if args.command == "import":
        return kit.source_import(caller, args.step, args.project, getattr(args, "source", ""), getattr(args, "repo_id", 0), getattr(args, "receipt", ""))
    if args.command == "issues":
        if caller is None or caller.role != "project":
            raise Refused("issues runs in a project bot's chat or job")
        return kit.issues(caller, args.claim)
    if args.command == "incidents":
        if caller is None or caller.role != "project":
            raise Refused("incidents runs in a project bot's chat or job")
        return kit.incidents(caller, args.ack)
    if args.command == "loop":
        if caller is None or caller.role not in LOOP_ACTIONS:
            raise Refused("loop runs in the chief of staff's or a project bot's chat or job")
        return kit.loop(caller, args.action, args.id, args.state, args.pr, args.waits_on, args.reported, args.all, args.evidence, args.dry_run, args.apply, args.part)
    if args.command == "handoff":
        if caller is None or caller.role != "chief":
            raise Refused("handoff runs in the chief of staff's chat or job")
        return kit.handoff(caller, args.project, args.part, args.decisions, args.outcome)
    if args.command == "andon":
        return kit.andon_command(caller, args.action, args.reason)
    return run_runner(args)


def failure_envelope(err: BaseException, secrets: tuple[str, ...]) -> dict:
    if isinstance(err, Coded):
        return envelope(err.code, clean(str(err), secrets), err.data, err.action, ok=False)
    if isinstance(err, MachineError):
        return envelope("machine_config_invalid", clean(str(err), secrets), None, "contact_support")
    if isinstance(err, (Refused, CloudError, PolicyError, AndonError, LoopError, InstallError, RunnerError)):
        return envelope("request_refused", clean(str(err), secrets))
    return envelope("unexpected", "Something unexpected went wrong on this machine.", None, "contact_support")


def main(argv: list[str] | None = None, run: Runner = run_gh, cloud: Cloud | None = None, bot: str | None = None,
         now: Callable[[], float] = time.time, fetch_api: FetchJson = fetch_json, fetch_url: FetchStatus = fetch_status,
         sleep: Callable[[float], None] = time.sleep, post: Poster = post_ping, tool: Tool = run_tool, refresher: Refresher = refresh,
         stdin: IO[bytes] | None = None, claude_state: ClaudeState = claude_cli_state, kanban_runner: Callable[[str], KanbanRunner] = hermes_runner) -> int:
    given = sys.argv[1:] if argv is None else argv
    try:
        args = parser().parse_args(argv)
    except UsageError as err:
        if given[:1] and given[0] in OWNER_ONLY:
            if "--json" in given:
                print(json.dumps(envelope("request_refused", OWNER_CREDENTIAL_NEEDED), ensure_ascii=False))
            else:
                print(f"faker-kit: refused: {OWNER_CREDENTIAL_NEEDED}", file=sys.stderr)
            return EXIT_REFUSED
        err.source.print_usage(sys.stderr)
        err.source.exit(2, f"{err.source.prog}: error: {err}\n")
    wants_json = bool(getattr(args, "json", False))
    kit: Kit | None = None
    try:
        kit = Kit(run, cloud, now=now, fetch_api=fetch_api, fetch_url=fetch_url, sleep=sleep, post=post, tool=tool, refresher=refresher, claude_state=claude_state, kanban_runner=kanban_runner)
        out = run_command(args, kit, bot or current_bot(), stdin or sys.stdin.buffer)
    except Exception as err:
        known = (Refused, CloudError, PolicyError, AndonError, LoopError, MachineError, InstallError, RunnerError, yaml.YAMLError)
        if wants_json:
            report = failure_envelope(err, kit.secrets_known() if kit else ())
            print(json.dumps(report, ensure_ascii=False))
            return EXIT_REFUSED
        if not isinstance(err, known):
            raise
        print(f"faker-kit: refused: {err}", file=sys.stderr)
        return EXIT_REFUSED
    if wants_json and isinstance(out, dict) and "ok" in out and "code" in out:
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["ok"] else EXIT_REFUSED
    print("\n".join(out) if isinstance(out, list) else json.dumps(out, ensure_ascii=False))
    if isinstance(out, dict) and out.get("status") == "ambiguous":
        print("faker-kit: refused: more than one pull request matches; nothing was merged, ask the owner which one and run --pr <number>", file=sys.stderr)
        return EXIT_AMBIGUOUS
    if isinstance(out, dict) and out.get("complete") is False:
        print("faker-kit: refused: not every open pull request merged; the lines above say which and why", file=sys.stderr)
        return EXIT_REFUSED
    return 0


def refuse_unexpected(kind: type[BaseException], _value: BaseException, _trace: TracebackType | None) -> None:
    print(f"faker-kit: refused: unexpected {kind.__name__}; nothing more was done", file=sys.stderr)


if __name__ == "__main__":
    sys.excepthook = refuse_unexpected
    sys.exit(main())
