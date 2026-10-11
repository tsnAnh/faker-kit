#!/usr/bin/env python3
"""team-kit-admin: the bootstrap bot's only command, and the installer's helper.

The bootstrap bot may run these, and the guard asks the owner to approve each one but `status`:

  status                                                     every project, repository, bot, and job, read-only
  add-project   --bot B --project P --repo R [--model M]     create or refresh a project bot for one repository:
                [--tick-every 10m] [--incidents-every 5m]    profile, skill, paused jobs, labels, bots.yaml
                [--routine trig_...]                         the Claude Code routine that starts its sessions
                [--claude-subscription]                      run the bot's own model on the owner's Claude
                                                             subscription, through the Claude Code CLI
  vendor        --project P                                  open a pull request that copies the pinned kit into the
                                                             repository, so cloud sessions have its skills
  setup-repo    --project P --backend claude|codex [--codex-env E]
                                                             start one cloud session that prepares the repository
  protect       --project P --check NAME [--check NAME ...]  require those checks on the default branch
  activate      --bot B                                      resume a project bot's jobs once its GitHub login is set
  add-chief     --bot B [--model M]                          create or refresh the chief of staff: reads across
                                                             projects and can propose a handoff with approval
  andon         [raise REASON... | clear]                    show, pull, or clear the machine's andon cord: nothing
                                                             new starts in any project, and agents at work stop

A project is one repository. Keep a product's apps (API, web, mobile) in one repository, so one
cloud agent can carry a feature through all of them in one pull request.

install.sh also runs these, which no bot may:

  add-bootstrap --bot B [--model M]                          create or refresh the bootstrap bot
  sync                                                       refresh every bot's files from the pinned kit

Bot names are free: any Hermes profile name. A bot's role lives in bots.yaml, which only this
command writes.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import yaml  # noqa: E402
from install_config import InstallError, apply_profile, config_set, cron_jobs, cron_spec, hermes, mapping, profile_home, value_at  # noqa: E402
from team_kit_andon import ANDON_LABEL, MACHINE, Andon, AndonError, clean_reason, issue_body  # noqa: E402
from team_kit_cloud import ROUTINE_TOKEN_ENV, Cloud, CloudError, Routine  # noqa: E402
from team_kit_contracts import ContractError, bootstrap_prompt, cloud_argv  # noqa: E402
from team_kit_guard.policy import (  # noqa: E402
    DEFAULT_INCIDENTS_EVERY,
    DEFAULT_TICK_EVERY,
    INTERVAL,
    NAME,
    REPO,
    RESERVED_NAMES,
    ROUTINE,
    Bot,
    Policy,
    PolicyError,
    load_policy,
    team_kit_home,
)

VENDOR_MANIFEST = ".agents/team-kit/KIT.json"
# The saved prompt of a project's routine. Each fire's payload is the task; the routine marks it as data,
# so the saved prompt is what tells the session to do it.
# The Hermes provider plugin that runs a bot's model through the owner's logged-in Claude Code CLI,
# so the bot needs no API key. It holds no credentials; the CLI's config directory holds the login.
CLAUDE_SUBSCRIPTION_PLUGIN = "claude-subscription-directsdk"
CLAUDE_SUBSCRIPTION_PROVIDER = "claude-subscription-directsdk-experimental"
CLAUDE_SUBSCRIPTION_DIR_ENV = "CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR"
CLAUDE_SUBSCRIPTION_MIN_CLI = (2, 1, 284)
CLAUDE_SUBSCRIPTION_DEFAULT_MODEL = "sonnet"
ROUTINE_PROMPT = ("You were started by the team-kit tick on the owner's machine. Do the task in the routine-fire-payload "
                  "block exactly as written; it is the owner's dispatched work. If there is no payload, say so and stop.")
LABELS = (
    ("agent-task", "1d76db", "A unit dispatched by a team-kit project bot"),
    ("agent:proposal", "c5def5", "A teammate proposal; the owner must restate the work in Hermes Desktop before any brief is written"),
    ("agent:running", "fbca04", "A cloud coding agent is working this unit"),
    ("agent:verifying", "5319e7", "A separate verifier session is checking this unit's pull request"),
    ("agent:review", "0e8a16", "Verified and the checks passed; the owner merges"),
    ("agent:needs-human", "d93f0b", "A dispatched unit needs the owner"),
    ("agent:security", "0052cc", "The tracking issue where Hermes's daily security reviews of the default branch post their results"),
    ("agent:incident", "e11d21", "An incident Hermes opened from polling: the default branch is red, or a health check fails"),
    ("risk:low", "c2e0c6", "Hermes's risk score for this pull request, from rules in code: small change in ordinary paths"),
    ("risk:medium", "fbca04", "Hermes's risk score for this pull request, from rules in code: read it with care"),
    ("risk:high", "b60205", "Hermes's risk score for this pull request, from rules in code: sensitive paths or a large change"),
    (ANDON_LABEL, "b60205", "The andon cord: agents stop, and nothing new starts until the owner clears it in Hermes"),
)
JOBS = ("team-kit-tick", "team-kit-status", "team-kit-security", "team-kit-incidents", "team-kit-upstream")
SCHEDULE_DEFAULTS = {"tick_every": DEFAULT_TICK_EVERY, "incidents_every": DEFAULT_INCIDENTS_EVERY}
SCHEDULE_FIELDS = {"team-kit-tick": "tick_every", "team-kit-incidents": "incidents_every"}
JOBS_PAUSED_REASON = "Awaiting the bot's GitHub login"

Runner = Callable[..., str]


class Refused(Exception):
    pass


def run_gh(args: list[str], input: str | None = None) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, input=input)
    if done.returncode != 0:
        raise Refused(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {done.stderr.strip()[:300]}")
    return done.stdout


class Admin:
    def __init__(self, run: Runner = run_gh, cloud: Cloud | None = None, home: Path | None = None):
        self.home = home or team_kit_home()
        self.root = self.home.parent
        self.kit = self.home / "kit"
        self.pkg = self.kit / "hermes"
        self.run = run
        self.cloud = cloud or Cloud(routines=self.routine_for)

    # bots.yaml -----------------------------------------------------------------------------------

    def bots_file(self) -> dict:
        path = self.home / "bots.yaml"
        data = yaml.safe_load(path.read_text()) if path.exists() else None
        data = data or {"version": 1, "bots": {}}
        data.setdefault("bots", {})
        return data

    def write_bots(self, data: dict) -> None:
        load_policy_from(self.home, data)
        path = self.home / "bots.yaml"
        tmp = path.with_name(f".bots.yaml.{os.getpid()}")
        tmp.write_text("# Written by team-kit-admin. Each key is a Hermes profile name.\n" + yaml.safe_dump(data, sort_keys=True, allow_unicode=True))
        tmp.chmod(0o600)
        tmp.replace(path)

    def policy(self) -> Policy:
        return load_policy(self.home)

    # files from the pinned kit -----------------------------------------------------------------------

    def ensure_profile(self, bot: str, role: str) -> Path:
        home = profile_home(self.root, bot)
        if not home.is_dir():
            hermes("-p", "default", "profile", "create", bot)
            if not home.is_dir():
                raise Refused(f"hermes profile create {bot} did not create {home}")
        return home

    def link_skill(self, home: Path, name: str) -> None:
        dest = home / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            print(f"conflict: {target} is a real directory; left as is", file=sys.stderr)
            return
        target.symlink_to(self.pkg / "skills" / name)

    def set_env(self, home: Path, key: str, value: str) -> None:
        env = home / ".env"
        lines = env.read_text().splitlines() if env.exists() else []
        kept = [line for line in lines if not line.startswith(f"{key}=")]
        env.write_text("\n".join([*kept, f"{key}={value}"]) + "\n")
        env.chmod(0o600)

    def env_value(self, home: Path, key: str) -> str:
        env = home / ".env"
        if not env.exists():
            return ""
        for line in env.read_text().splitlines():
            if line.startswith(f"{key}="):
                value = line.split("=", 1)[1].strip()
                return value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"" else value
        return ""

    def has_env(self, home: Path, key: str) -> bool:
        return bool(self.env_value(home, key))

    def routine_for(self, repo: str) -> Routine | None:
        """The routine of the project bot that owns repo, with the token from that bot's own .env."""
        bot = next((b for b in self.policy().bots.values() if b.role == "project" and b.owns(repo)), None)
        if bot is None or not bot.routine:
            return None
        return Routine(bot.routine, self.env_value(profile_home(self.root, bot.name), ROUTINE_TOKEN_ENV))

    def setup_files(self, bot: str, role: str, project: str | None, model: str | None, claude_config_dir: str | None = None) -> None:
        """claude_config_dir, from claude_login(), switches the bot's model to the Claude subscription; None leaves the provider alone."""
        home = self.ensure_profile(bot, role)
        apply_profile(self.root, bot, self.pkg / "profiles" / role / "config.yaml")
        if claude_config_dir is not None:
            self.use_claude_subscription(bot, home, model, claude_config_dir)
        elif model:
            config_set(self.root, bot, ("model", "default"), model)
        soul = self.pkg / "profiles" / role / "SOUL.md"
        if (home / "SOUL.md").exists() and (home / "SOUL.md").read_text() != soul.read_text():
            shutil.copy2(home / "SOUL.md", home / "SOUL.md.bak")
        shutil.copy2(soul, home / "SOUL.md")
        plugin = home / "plugins" / "team_kit_guard"
        shutil.rmtree(plugin, ignore_errors=True)
        shutil.copytree(self.pkg / "plugin" / "team_kit_guard", plugin, ignore=shutil.ignore_patterns("__pycache__"))
        self.link_skill(home, {"project": "dispatch", "bootstrap": "bootstrap", "chief": "chief"}[role])
        if role == "chief":
            (self.home / "drafts" / bot).mkdir(parents=True, exist_ok=True)
            (self.home / "drafts" / bot).chmod(0o700)
        if role == "project":
            (self.home / "briefs" / bot).mkdir(parents=True, exist_ok=True)
            (self.home / "proposals" / bot).mkdir(parents=True, exist_ok=True)
            # An earlier version kept webhook triage scripts here; nothing uses them now.
            shutil.rmtree(home / "scripts" / "team-kit", ignore_errors=True)

    def claude_login(self) -> str:
        """The Claude Code CLI's config directory, once the CLI is new enough and logged in there.
        Empty means the CLI's default. The gateway does not inherit the owner's shell, so the bot's .env carries it."""
        claude = shutil.which("claude")
        if not claude:
            raise Refused("Claude Code is not installed: npm install -g @anthropic-ai/claude-code, then claude auth login")
        try:
            version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30).stdout
            status = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("Claude Code did not answer --version or auth status") from None
        found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", version)
        if not found or tuple(int(n) for n in found.groups()) < CLAUDE_SUBSCRIPTION_MIN_CLI:
            raise Refused(f"Claude Code {'.'.join(map(str, CLAUDE_SUBSCRIPTION_MIN_CLI))} or later is needed; update it with npm install -g @anthropic-ai/claude-code")
        try:
            logged_in = json.loads(status).get("loggedIn") is True
        except (ValueError, AttributeError):
            logged_in = False
        if not logged_in:
            raise Refused("Claude Code is not logged in here: run claude auth login, then run this again")
        return os.environ.get("CLAUDE_CONFIG_DIR", "")

    def use_claude_subscription(self, bot: str, home: Path, model: str | None, config_dir: str) -> None:
        if not (home / "plugins" / CLAUDE_SUBSCRIPTION_PROVIDER).is_dir():
            hermes("-p", bot, "plugins", "install", CLAUDE_SUBSCRIPTION_PLUGIN)
        hermes("-p", bot, "plugins", "enable", CLAUDE_SUBSCRIPTION_PROVIDER, "--no-allow-tool-override")
        config_set(self.root, bot, ("model", "provider"), CLAUDE_SUBSCRIPTION_PROVIDER)
        config_set(self.root, bot, ("model", "default"), model or CLAUDE_SUBSCRIPTION_DEFAULT_MODEL)
        current = mapping(home / "config.yaml")
        for key in ("base_url", "api_mode"):  # the previous provider's endpoint must not reach the plugin
            if value_at(current, ("model", key)) is not None:
                hermes("-p", bot, "config", "unset", f"model.{key}")
        self.set_env(home, CLAUDE_SUBSCRIPTION_DIR_ENV, config_dir)

    def set_env(self, home: Path, key: str, value: str) -> None:
        """Set or remove one non-secret line in a profile's .env, keeping every other line and mode 600."""
        env = home / ".env"
        lines = [line for line in (env.read_text().splitlines() if env.exists() else []) if not line.startswith(f"{key}=")]
        if value:
            lines.append(f"{key}={value}")
        if not lines and not env.exists():
            return
        tmp = env.with_name(f".env.{os.getpid()}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write("".join(f"{line}\n" for line in lines))
        tmp.replace(env)

    def create_job(self, bot: str, job: dict, enabled: bool) -> None:
        args = ["-p", bot, "cron", "create", job["schedule"], job["prompt"], "--name", job["name"], "--deliver", job["deliver"], "--skill", job["skill"]]
        if not enabled:
            args += ["--paused", "--paused-reason", JOBS_PAUSED_REASON]
        hermes(*args)

    def ensure_jobs(self, bot: Bot) -> tuple[list[str], list[str]]:
        existing = cron_jobs(hermes("-p", bot.name, "cron", "list", "--all"))
        created, rescheduled = [], []
        for job in cron_spec(self.pkg / "cron" / "project-jobs.md", bot.name):
            field = SCHEDULE_FIELDS.get(job["name"])
            if field:
                job = {**job, "schedule": f"every {getattr(bot, field)}"}
            current = existing.get(job["name"])
            if current and current.get("schedule") == job["schedule"]:
                continue
            if current and current.get("schedule"):
                # Edit in place: the job keeps its id, its enabled or paused state, and its paused reason.
                hermes("-p", bot.name, "cron", "edit", current["id"], "--schedule", job["schedule"])
                rescheduled.append(job["name"])
                continue
            if current:
                continue
            self.create_job(bot.name, job, False)
            created.append(job["name"])
        return created, rescheduled

    # commands --------------------------------------------------------------------------------------

    def add_bootstrap(self, bot: str, model: str | None, claude_subscription: bool = False) -> dict:
        check_name(bot, "bot")
        claude_config_dir = self.claude_login() if claude_subscription else None  # refuse before anything changes
        data = self.bots_file()
        current = data["bots"].get(bot)
        if current and current.get("role") != "bootstrap":
            raise Refused(f"{bot} is already a {current.get('role')} bot")
        data["bots"][bot] = {"role": "bootstrap"}
        load_policy_from(self.home, data)
        self.setup_files(bot, "bootstrap", None, model, claude_config_dir)
        self.write_bots(data)
        return {"bot": bot, "role": "bootstrap"}

    def add_chief(self, bot: str, model: str | None, claude_subscription: bool = False) -> dict:
        check_name(bot, "bot")
        claude_config_dir = self.claude_login() if claude_subscription else None  # refuse before anything changes
        data = self.bots_file()
        current = data["bots"].get(bot)
        if current and current.get("role") != "chief":
            raise Refused(f"{bot} is already a {current.get('role')} bot")
        data["bots"][bot] = {"role": "chief"}
        load_policy_from(self.home, data)
        self.setup_files(bot, "chief", None, model, claude_config_dir)
        self.write_bots(data)
        repos = sorted(b.repo for b in self.policy().projects().values())
        return {
            "bot": bot, "role": "chief",
            "next": [f"The owner puts a read-only GitHub token in {profile_home(self.root, bot) / '.env'} as GH_TOKEN: "
                     "Issues, Contents, and Metadata read on every project's repository" + (f" ({', '.join(repos)})" if repos else ""),
                     "Add the bot in Hermes Desktop Bot Mode"],
        }

    def add_project(self, bot: str, project: str, repo: str, model: str | None, tick_every: str | None, incidents_every: str | None,
                    routine: str | None = None, claude_subscription: bool = False) -> dict:
        check_name(bot, "bot")
        check_name(project, "project")
        check_repo(repo)
        check_interval(tick_every, "--tick-every")
        check_interval(incidents_every, "--incidents-every")
        if routine is not None and not ROUTINE.match(routine):
            raise Refused("--routine must be a Claude Code routine id like trig_01ABC..., from its page at claude.ai/code/routines")
        claude_config_dir = self.claude_login() if claude_subscription else None  # refuse before anything changes
        data = self.bots_file()
        current = data["bots"].get(bot)
        if current and (current.get("role") != "project" or current.get("project") != project):
            raise Refused(f"{bot} is already the {current.get('role')} bot{' of ' + current['project'] if current.get('project') else ''}")
        if current and str(current.get("repo", "")).lower() != repo.lower():
            raise Refused(f"project {project} is {current.get('repo')}; a project has one repository, so start another project for {repo}")
        entry = {"role": "project", "project": project, "repo": current.get("repo") if current else repo}
        for key, value in (("tick_every", tick_every), ("incidents_every", incidents_every)):
            effective = value if value is not None else (current or {}).get(key)
            if effective and effective != SCHEDULE_DEFAULTS[key]:
                entry[key] = effective
        routine = routine or (current or {}).get("routine")
        if routine:
            entry["routine"] = routine
        data["bots"][bot] = entry
        project_bot = load_policy_from(self.home, data).bot(bot)  # refuses a repository another project owns
        for name, color, description in LABELS:
            self.run(["label", "create", name, "-R", repo, "--color", color, "--description", description, "--force"])
        self.setup_files(bot, "project", project, model, claude_config_dir)
        created, rescheduled = self.ensure_jobs(project_bot)
        self.write_bots(data)
        return {
            "bot": bot, "project": project, "repo": repo, "labels": [n for n, _, _ in LABELS], "jobs_created_paused": created,
            "jobs_rescheduled": rescheduled, "tick_every": project_bot.tick_every, "incidents_every": project_bot.incidents_every,
            "routine": project_bot.routine,
            "next": [*([] if project_bot.routine else [
                         f"For Claude Code sessions, the owner creates a routine on {repo} at claude.ai/code/routines whose prompt is "
                         f"\"{ROUTINE_PROMPT}\", turns it on, adds an API trigger, and reruns add-project with --routine trig_..."]),
                     f"The owner puts the routine's API token in {profile_home(self.root, bot) / '.env'} as {ROUTINE_TOKEN_ENV}",
                     f"team-kit-admin setup-repo --project {project} --backend claude|codex",
                     f"The owner puts the bot's GitHub token in {profile_home(self.root, bot) / '.env'} as GH_TOKEN",
                     f"team-kit-admin activate --bot {bot}"],
        }

    def project_bot(self, policy: Policy, project: str):
        bot = policy.projects().get(project)
        if bot is None:
            raise Refused(f"no project {project}; create it with add-project first")
        return bot

    def api(self, path: str, method: str = "GET", body: dict | None = None):
        args = ["api", "-X", method, path] if method != "GET" else ["api", path]
        if body is not None:
            args += ["--input", "-"]
        out = self.run(args, input=json.dumps(body)) if body is not None else self.run(args)
        return json.loads(out) if out.strip() else None

    def vendored_ref(self, repo: str, branch: str) -> str | None:
        """The kit commit vendored on a branch, or None."""
        try:
            data = self.api(f"repos/{repo}/contents/{VENDOR_MANIFEST}?ref={quote(branch, safe='')}")
            return json.loads(base64.b64decode(data["content"]))["kit_ref"]
        except (Refused, ValueError, KeyError, TypeError):
            return None

    def vendor_module(self):
        """vendor_kit.py from the pinned kit, so the copy and its manifest match that commit."""
        path = self.kit / "plugins" / "trust-ladder" / "skills" / "setup-cloud-agents" / "scripts" / "vendor_kit.py"
        if not path.is_file():
            raise Refused(f"the kit at {path.parents[5]} has no vendor_kit.py; pin a newer kit commit")
        spec = importlib.util.spec_from_file_location("team_kit_vendor_kit", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up while the class is built
        writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # leave the kit checkout clean
        try:
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = writes
        return module

    def vendor(self, project: str) -> dict:
        """Commit the pinned kit into the project's repository through the GitHub API, on a branch,
        and open a pull request. No git push, so the Hermes machine needs no git credentials."""
        repo = self.project_bot(self.policy(), project).repo
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

        def link_target(entry) -> str:
            blob = self.api(f"repos/{repo}/git/blobs/{entry['sha']}")
            return base64.b64decode(blob["content"]).decode()

        targets: dict[str, bool] = {}

        def ours(entry) -> bool:
            if entry.get("mode") != "120000":
                return False
            if entry["sha"] not in targets:
                targets[entry["sha"]] = vk.points_into_vendor(link_target(entry))
            return targets[entry["sha"]]

        entries = [{"path": path, "mode": "100755" if executable else "100644", "type": "blob", "content": data.decode()}
                   for path, (data, executable) in sorted(plan.files.items())]
        stale = [path for path, e in existing.items() if path.startswith(f"{vk.VENDOR_DIR}/") and e["type"] == "blob"
                 and path not in plan.files and path != vk.MANIFEST]
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
        if tree == base_tree:
            return {**result, "pr": None, "note": f"{default} already has the kit at {kit_ref[:12]}"}
        short = kit_ref[:12]
        commit = self.api(f"repos/{repo}/git/commits", "POST",
                          {"message": f"chore(agents): vendor team-kit {short}", "tree": tree, "parents": [base]})["sha"]
        branch = f"team-kit/kit-{short}"
        try:
            self.api(f"repos/{repo}/git/ref/heads/{quote(branch, safe='/')}")
            self.api(f"repos/{repo}/git/refs/heads/{quote(branch, safe='/')}", "PATCH", {"sha": commit, "force": True})
        except Refused:
            self.api(f"repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": commit})
        open_prs = [pr for pr in self.api(f"repos/{repo}/pulls?state=open&base={ref_query}&per_page=100") or []
                    if str((pr.get("head") or {}).get("ref", "")).startswith("team-kit/kit-")]
        mine = [pr["html_url"] for pr in open_prs if pr["head"]["ref"] == branch]
        superseded = sorted(pr["html_url"] for pr in open_prs if pr["head"]["ref"] != branch)
        if mine:
            url = mine[0]
        else:
            body = (f"Copies the team kit at `{kit_ref}` into `.agents/team-kit/`, and links its skills from `.claude/skills/` and "
                    "`.agents/skills/` and its subagents from `.claude/agents/`, so every agent session, cloud sessions included, "
                    "has them. Generated by `team-kit-admin vendor`; nothing under `.agents/team-kit/` is edited by hand.\n\n"
                    f"`python3 .agents/team-kit/vendor_kit.py --check .` verifies the copy. Kept the repository's own: "
                    f"{', '.join(skipped) or 'none'}. Removed: {', '.join(sorted(stale)) or 'nothing'}.")
            url = self.api(f"repos/{repo}/pulls", "POST", {"title": f"chore(agents): vendor team-kit {short}", "head": branch,
                                                         "base": default, "body": body})["html_url"]
        return {**result, "branch": branch, "pr": url, "superseded": superseded}

    def setup_repo(self, project: str, backend: str, codex_env: str | None) -> dict:
        policy = self.policy()
        bot = self.project_bot(policy, project)
        repo = bot.repo
        if backend == "codex" and not codex_env:
            raise Refused("codex needs --codex-env, the Codex cloud environment id for this repository")
        commit = git(self.kit, "rev-parse", "HEAD")
        default = self.api(f"repos/{repo}")["default_branch"]
        vendored = self.vendored_ref(repo, default)
        if vendored != commit:
            found = f"has the kit at {vendored[:12]}" if vendored else "has no vendored kit"
            raise Refused(f"{repo} {default} {found}, not {commit[:12]}; run team-kit-admin vendor --project {project} and merge its pull request first")
        cord = Andon(self.home).machine()
        if cord:
            raise Refused(f"{cord.describe()}; clear it before starting a setup session")
        prompt = bootstrap_prompt(repo, project, backend, commit, sorted(policy.trusted_authors), codex_env)
        command = cloud_argv(backend, prompt, 1, codex_env)
        owner, name = repo.split("/")
        try:
            session = self.cloud.start(backend, repo, command, self.home / "checkouts" / f"{owner}__{name}" / "setup")
        except CloudError as err:
            raise Refused(f"setup launch failed: {err}") from err
        return {"project": project, "repo": repo, "backend": backend, "kit_ref": commit, "session": session}

    def protect(self, project: str, checks: list[str]) -> dict:
        repo = self.project_bot(self.policy(), project).repo
        if not checks:
            raise Refused("name at least one --check, such as the CI job that runs scripts/check.sh")
        branch = json.loads(self.run(["api", f"repos/{repo}"]))["default_branch"]
        body = {"required_status_checks": {"strict": False, "contexts": checks}, "enforce_admins": True,
                "required_pull_request_reviews": {"required_approving_review_count": 0}, "restrictions": None}
        self.run(["api", "-X", "PUT", f"repos/{repo}/branches/{branch}/protection", "--input", "-"], input=json.dumps(body))
        return {"repo": repo, "branch": branch, "required_checks": checks}

    def activate(self, bot: str) -> dict:
        policy = self.policy()
        me = policy.bot(bot)
        if me.role != "project":
            raise Refused(f"{bot} is not a project bot")
        if not self.has_env(profile_home(self.root, bot), "GH_TOKEN"):
            raise Refused(f"{bot} has no GH_TOKEN in {profile_home(self.root, bot) / '.env'}")
        resumed = []
        for name, job in cron_jobs(hermes("-p", bot, "cron", "list", "--all")).items():
            if name in JOBS and not job["enabled"]:
                hermes("-p", bot, "cron", "resume", job["id"])
                resumed.append(name)
        return {"bot": bot, "resumed": resumed}

    # andon -----------------------------------------------------------------------------------------

    def andon_show(self) -> dict:
        andon, policy = Andon(self.home), self.policy()
        machine = andon.read(MACHINE)
        projects = {name: andon.project(name).reason for name, bot in sorted(policy.bots.items()) if bot.role == "project" and andon.project(name)}
        return {"machine": None if machine is None else {k: machine.get(k) for k in ("reason", "by", "issues")}, "projects": projects}

    def andon_raise(self, words: list[str], who: str | None) -> dict:
        why = clean_reason(words)
        andon, policy = Andon(self.home), self.policy()
        if andon.machine():
            raise Refused(f"{andon.machine().describe()}; clear it before pulling it again")
        # Write the cord first: nothing new starts from here on, whatever GitHub does.
        record = {"reason": why, "by": who or "the owner", "at": time.time(), "issues": {}}
        andon.write(MACHINE, record)
        failed = {}
        for bot in sorted(policy.bots.values(), key=lambda b: b.name):
            if bot.role != "project" or not bot.repo:
                continue
            try:
                url = self.run(["issue", "create", "-R", bot.repo, "--title", f"Andon: {why}"[:120], "--label", ANDON_LABEL,
                                "--body", issue_body("machine", why, "team-kit-admin andon clear")]).strip()
                record["issues"][bot.repo] = int(url.rstrip("/").rsplit("/", 1)[-1])
            except Exception as err:  # noqa: BLE001 - the local cord still stops every launch there
                failed[bot.repo] = str(err)[:200]
            andon.write(MACHINE, record)
        out: dict = {"stopped": True, "reason": why, "issues": record["issues"]}
        if failed:
            out["no_issue"] = failed
        return out

    def andon_clear(self) -> dict:
        andon = Andon(self.home)
        record = andon.read(MACHINE)
        if record is None:
            raise Refused("the machine's andon cord is not pulled")
        closed, failed = [], {}
        for repo, number in sorted((record.get("issues") or {}).items()):
            try:
                self.run(["issue", "close", str(int(number)), "-R", repo, "--comment", "The owner cleared the machine's andon cord in Hermes. The line runs again."])
                closed.append(f"{repo}#{number}")
            except Exception as err:  # noqa: BLE001 - already closed by hand, or gone
                failed[f"{repo}#{number}"] = str(err)[:200]
        andon.drop(MACHINE)
        out: dict = {"stopped": False, "closed": closed}
        if failed:
            out["not_closed"] = failed
        still = self.andon_show()["projects"]
        if still:
            out["projects_still_stopped"] = still
        return out

    def status(self) -> dict:
        policy = self.policy()
        out = {"max_in_flight_total": policy.max_in_flight_total, "trusted_authors": sorted(policy.trusted_authors), "bots": []}
        andon = self.andon_show()
        if andon["machine"] or andon["projects"]:
            out["andon"] = andon
        for bot in sorted(policy.bots.values(), key=lambda b: b.name):
            item = {"bot": bot.name, "role": bot.role}
            if bot.role == "project":
                item.update(project=bot.project, tick_every=bot.tick_every, incidents_every=bot.incidents_every,
                            github_login=self.has_env(profile_home(self.root, bot.name), "GH_TOKEN"),
                            routine=bot.routine, routine_token=self.has_env(profile_home(self.root, bot.name), ROUTINE_TOKEN_ENV))
                try:
                    item["jobs"] = {n: j["enabled"] for n, j in cron_jobs(hermes("-p", bot.name, "cron", "list", "--all")).items() if n.startswith("team-kit-")}
                except InstallError as err:
                    item["jobs"] = f"unreadable: {err}"
                item["repo"] = self.repo_status(bot.repo)
                if isinstance(item["repo"].get("running"), int):
                    # This login sees every project, so it refreshes each project bot's count for the
                    # machine-wide limit, even for a bot whose jobs are paused.
                    write_in_flight(self.home, bot.name, {bot.repo: item["repo"]["running"]})
            out["bots"].append(item)
        return out

    def repo_status(self, repo: str) -> dict:
        item: dict = {"repo": repo}
        try:
            names = {label["name"] for label in json.loads(self.run(["api", f"repos/{repo}/labels?per_page=100"]))}
            item["labels_missing"] = [n for n, _, _ in LABELS if n not in names]
            # A slot is held by a coding agent or a verifier.
            item["running"] = len({int(i["number"]) for label in ("agent:running", "agent:verifying")
                                   for i in json.loads(self.run(["issue", "list", "-R", repo, "--label", label, "--state", "open", "--json", "number", "--limit", "200"]))})
            try:
                self.run(["api", f"repos/{repo}/contents/.agents/dispatch.json"])
                item["dispatch_json"] = True
            except Refused:
                item["dispatch_json"] = False
        except (Refused, ValueError, KeyError) as err:
            item["error"] = str(err)[:200]
        return item

    def sync(self) -> dict:
        policy = self.policy()
        done = []
        for bot in sorted(policy.bots.values(), key=lambda b: b.name):
            self.setup_files(bot.name, bot.role, bot.project, None)
            if bot.role == "project":
                self.ensure_jobs(bot)
            done.append(bot.name)
        return {"synced": done}


def write_in_flight(home: Path, bot: str, counts: dict[str, int]) -> None:
    """Under the same lock team-kit-gh launch holds, so this never overwrites a launch's count."""
    directory = home / "in-flight"
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        target = directory / f"{bot}.json"
        tmp = target.with_name(f".{target.name}.{os.getpid()}")
        tmp.write_text(json.dumps({"at": time.time(), "repos": counts}))
        tmp.replace(target)


def load_policy_from(home: Path, bots: dict) -> Policy:
    """Validate a bots.yaml mapping against guard.yaml before anything is written."""
    import yaml as _yaml

    from team_kit_guard.policy import parse_policy

    try:
        guard = _yaml.safe_load((home / "guard.yaml").read_text())
    except OSError:
        raise Refused(f"no guard.yaml in {home}; run hermes/install.sh") from None
    return parse_policy(guard, bots, home)


def check_name(value: str, what: str) -> None:
    if what == "bot" and value in RESERVED_NAMES:
        raise Refused(f"bot {value!r} is the owner's own Hermes profile; pick another name")
    if not NAME.match(value or ""):
        raise Refused(f"{what} {value!r} must be lowercase letters, digits, - and _, starting with a letter or digit; give the bot any title you like in Bot Mode")


def check_repo(value: str) -> None:
    if not REPO.match(value or ""):
        raise Refused(f"repository {value!r} is not OWNER/REPO")


def check_interval(value: str | None, what: str) -> None:
    if value is not None and not INTERVAL.match(value):
        raise Refused(f"{what} must be a positive interval like 10m or 1h")


def git(path: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"no kit checkout at {path}; run hermes/install.sh")
    return done.stdout.strip()


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="team-kit-admin")
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("sync")
    cmd = sub.add_parser("add-bootstrap")
    cmd.add_argument("--bot", required=True)
    cmd.add_argument("--model")
    cmd.add_argument("--claude-subscription", action="store_true")
    cmd = sub.add_parser("add-project")
    cmd.add_argument("--bot", required=True)
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--repo", required=True, help="the project's one OWNER/REPO")
    cmd.add_argument("--model")
    cmd.add_argument("--tick-every")
    cmd.add_argument("--incidents-every")
    cmd.add_argument("--routine")
    cmd.add_argument("--claude-subscription", action="store_true")
    cmd = sub.add_parser("vendor")
    cmd.add_argument("--project", required=True)
    cmd = sub.add_parser("setup-repo")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--backend", required=True, choices=["claude", "codex"])
    cmd.add_argument("--codex-env")
    cmd = sub.add_parser("protect")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--check", action="append", default=[])
    cmd = sub.add_parser("activate")
    cmd.add_argument("--bot", required=True)
    cmd = sub.add_parser("add-chief")
    cmd.add_argument("--bot", required=True)
    cmd.add_argument("--model")
    cmd.add_argument("--claude-subscription", action="store_true")
    cmd = sub.add_parser("andon")
    cmd.add_argument("action", nargs="?", choices=["show", "raise", "clear"], default="show")
    cmd.add_argument("reason", nargs="*")
    return top


INSTALL_ONLY = ("add-bootstrap", "sync")


def caller() -> str | None:
    """The Hermes profile running this command, or None from a plain shell."""
    value = os.environ.get("HERMES_HOME")
    if not value:
        return None
    home = Path(value)
    # The host profile is the owner's own Hermes, not a team-kit bot.
    return home.name if home.parent.name == "profiles" else None


def check_caller(admin: Admin, command: str, who: str | None) -> None:
    """The guard is the gate; this is the second lock. Only the bootstrap bot or a plain shell may run
    team-kit-admin, and only a plain shell may run the install-only commands."""
    if who is None:
        return
    bots = admin.bots_file()["bots"]
    role = (bots.get(who) or {}).get("role")
    if role != "bootstrap":
        raise Refused(f"profile {who} is not a bootstrap bot; only the bootstrap bot runs team-kit-admin")
    if command in INSTALL_ONLY:
        raise Refused(f"{command} runs only from hermes/install.sh")


def main(argv: list[str] | None = None, run: Runner = run_gh, cloud: Cloud | None = None, who: str | None = ...) -> int:
    args = parser().parse_args(argv)
    admin = Admin(run=run, cloud=cloud)
    who = caller() if who is ... else who
    try:
        check_caller(admin, args.command, who)
        if args.command == "status":
            out = admin.status()
        elif args.command == "sync":
            out = admin.sync()
        elif args.command == "add-bootstrap":
            out = admin.add_bootstrap(args.bot, args.model, args.claude_subscription)
        elif args.command == "add-project":
            out = admin.add_project(args.bot, args.project, args.repo, args.model, args.tick_every, args.incidents_every, args.routine,
                                    args.claude_subscription)
        elif args.command == "add-chief":
            out = admin.add_chief(args.bot, args.model, args.claude_subscription)
        elif args.command == "vendor":
            out = admin.vendor(args.project)
        elif args.command == "setup-repo":
            out = admin.setup_repo(args.project, args.backend, args.codex_env)
        elif args.command == "protect":
            out = admin.protect(args.project, args.check)
        elif args.command == "andon":
            if args.action != "raise" and args.reason:
                raise Refused(f"andon {args.action} takes no reason")
            out = admin.andon_raise(args.reason, who) if args.action == "raise" else admin.andon_clear() if args.action == "clear" else admin.andon_show()
        else:
            out = admin.activate(args.bot)
    except (Refused, InstallError, PolicyError, ContractError, AndonError, yaml.YAMLError) as err:
        print(f"team-kit-admin: refused: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001 - the bot gets one refusal line, never a traceback
        print(f"team-kit-admin: refused: unexpected {type(err).__name__}: {str(err)[:300]}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
