#!/usr/bin/env python3
"""team-kit-admin: the bootstrap bot's only command, and the installer's helper.

The bootstrap bot may run these, and the guard asks the owner to approve each one but `status`:

  status                                                     every project, repository, bot, and job, read-only
  add-project   --bot B --project P --repo R [--model M]     create or refresh a project bot for one repository:
                                                             profile, skill, paused jobs, labels, bots.yaml
  setup-repo    --project P --backend claude|codex [--codex-env E]
                                                             start one cloud session that prepares the repository
  protect       --project P --check NAME [--check NAME ...]  require those checks on the default branch
  activate      --bot B                                      resume a project bot's jobs once its GitHub login is set

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
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import yaml  # noqa: E402
from install_config import InstallError, apply_profile, config_set, cron_jobs, cron_spec, hermes, profile_home  # noqa: E402
from team_kit_cloud import Cloud, CloudError  # noqa: E402
from team_kit_contracts import ContractError, bootstrap_prompt, cloud_argv  # noqa: E402
from team_kit_guard.policy import NAME, REPO, RESERVED_NAMES, Policy, PolicyError, load_policy, team_kit_home  # noqa: E402

LABELS = (
    ("agent-task", "1d76db", "A unit dispatched by a team-kit project bot"),
    ("agent:running", "fbca04", "A cloud coding agent is working this unit"),
    ("agent:review", "0e8a16", "The agent is done and the checks passed; the owner merges"),
    ("agent:needs-human", "d93f0b", "A dispatched unit needs the owner"),
)
JOBS = ("team-kit-tick", "team-kit-status")
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
        self.cloud = cloud or Cloud(run_gh=run)

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

    def has_env(self, home: Path, key: str) -> bool:
        env = home / ".env"
        return env.exists() and any(line.startswith(f"{key}=") and line.split("=", 1)[1].strip() for line in env.read_text().splitlines())

    def setup_files(self, bot: str, role: str, project: str | None, model: str | None) -> None:
        home = self.ensure_profile(bot, role)
        apply_profile(self.root, bot, self.pkg / "profiles" / role / "config.yaml")
        if model:
            config_set(self.root, bot, ("model", "default"), model)
        soul = self.pkg / "profiles" / role / "SOUL.md"
        if (home / "SOUL.md").exists() and (home / "SOUL.md").read_text() != soul.read_text():
            shutil.copy2(home / "SOUL.md", home / "SOUL.md.bak")
        shutil.copy2(soul, home / "SOUL.md")
        plugin = home / "plugins" / "team_kit_guard"
        shutil.rmtree(plugin, ignore_errors=True)
        shutil.copytree(self.pkg / "plugin" / "team_kit_guard", plugin, ignore=shutil.ignore_patterns("__pycache__"))
        self.link_skill(home, "dispatch" if role == "project" else "bootstrap")
        if role == "project":
            (self.home / "briefs" / bot).mkdir(parents=True, exist_ok=True)
            # An earlier version kept webhook triage scripts here; nothing uses them now.
            shutil.rmtree(home / "scripts" / "team-kit", ignore_errors=True)

    def ensure_jobs(self, bot: str) -> list[str]:
        existing = cron_jobs(hermes("-p", bot, "cron", "list", "--all"))
        created = []
        for job in cron_spec(self.pkg / "cron" / "project-jobs.md", bot):
            if job["name"] in existing:
                continue
            hermes("-p", bot, "cron", "create", job["schedule"], job["prompt"], "--name", job["name"], "--deliver", job["deliver"],
                   "--skill", job["skill"], "--paused", "--paused-reason", JOBS_PAUSED_REASON)
            created.append(job["name"])
        return created

    # commands --------------------------------------------------------------------------------------

    def add_bootstrap(self, bot: str, model: str | None) -> dict:
        check_name(bot, "bot")
        data = self.bots_file()
        current = data["bots"].get(bot)
        if current and current.get("role") != "bootstrap":
            raise Refused(f"{bot} is already a {current.get('role')} bot")
        data["bots"][bot] = {"role": "bootstrap"}
        load_policy_from(self.home, data)
        self.setup_files(bot, "bootstrap", None, model)
        self.write_bots(data)
        return {"bot": bot, "role": "bootstrap"}

    def add_project(self, bot: str, project: str, repo: str, model: str | None) -> dict:
        check_name(bot, "bot")
        check_name(project, "project")
        check_repo(repo)
        data = self.bots_file()
        current = data["bots"].get(bot)
        if current and (current.get("role") != "project" or current.get("project") != project):
            raise Refused(f"{bot} is already the {current.get('role')} bot{' of ' + current['project'] if current.get('project') else ''}")
        if current and str(current.get("repo", "")).lower() != repo.lower():
            raise Refused(f"project {project} is {current.get('repo')}; a project has one repository, so start another project for {repo}")
        data["bots"][bot] = {"role": "project", "project": project, "repo": current.get("repo") if current else repo}
        load_policy_from(self.home, data)  # refuses a repository another project owns
        for name, color, description in LABELS:
            self.run(["label", "create", name, "-R", repo, "--color", color, "--description", description, "--force"])
        self.setup_files(bot, "project", project, model)
        created = self.ensure_jobs(bot)
        self.write_bots(data)
        return {
            "bot": bot, "project": project, "repo": repo, "labels": [n for n, _, _ in LABELS], "jobs_created_paused": created,
            "next": [f"team-kit-admin setup-repo --project {project} --backend claude|codex",
                     f"The owner puts the bot's GitHub token in {profile_home(self.root, bot) / '.env'} as GH_TOKEN",
                     f"team-kit-admin activate --bot {bot}"],
        }

    def project_bot(self, policy: Policy, project: str):
        bot = policy.projects().get(project)
        if bot is None:
            raise Refused(f"no project {project}; create it with add-project first")
        return bot

    def setup_repo(self, project: str, backend: str, codex_env: str | None) -> dict:
        policy = self.policy()
        bot = self.project_bot(policy, project)
        repo = bot.repo
        if backend == "codex" and not codex_env:
            raise Refused("codex needs --codex-env, the Codex cloud environment id for this repository")
        commit = git(self.kit, "rev-parse", "HEAD")
        source = os.environ.get("TEAM_KIT_SOURCE_URL") or git(self.kit, "remote", "get-url", "origin")
        if not source.startswith("https://"):
            raise Refused(f"kit source {source} is not an https URL a cloud session can clone; set TEAM_KIT_SOURCE_URL")
        prompt = bootstrap_prompt(repo, project, backend, source, commit, sorted(policy.trusted_authors), codex_env)
        argv = cloud_argv(backend, prompt, 1, codex_env)
        owner, name = repo.split("/")
        try:
            session = self.cloud.start(backend, repo, argv, self.home / "checkouts" / f"{owner}__{name}" / "setup")
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

    def status(self) -> dict:
        policy = self.policy()
        out = {"max_in_flight_total": policy.max_in_flight_total, "trusted_authors": sorted(policy.trusted_authors), "bots": []}
        for bot in sorted(policy.bots.values(), key=lambda b: b.name):
            item = {"bot": bot.name, "role": bot.role}
            if bot.role == "project":
                item.update(project=bot.project, github_login=self.has_env(profile_home(self.root, bot.name), "GH_TOKEN"))
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
            item["running"] = len(json.loads(self.run(["issue", "list", "-R", repo, "--label", "agent:running", "--state", "open", "--json", "number", "--limit", "200"])))
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
                self.ensure_jobs(bot.name)
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
    cmd = sub.add_parser("add-project")
    cmd.add_argument("--bot", required=True)
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--repo", required=True, help="the project's one OWNER/REPO")
    cmd.add_argument("--model")
    cmd = sub.add_parser("setup-repo")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--backend", required=True, choices=["claude", "codex"])
    cmd.add_argument("--codex-env")
    cmd = sub.add_parser("protect")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--check", action="append", default=[])
    cmd = sub.add_parser("activate")
    cmd.add_argument("--bot", required=True)
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
    try:
        check_caller(admin, args.command, caller() if who is ... else who)
        if args.command == "status":
            out = admin.status()
        elif args.command == "sync":
            out = admin.sync()
        elif args.command == "add-bootstrap":
            out = admin.add_bootstrap(args.bot, args.model)
        elif args.command == "add-project":
            out = admin.add_project(args.bot, args.project, args.repo, args.model)
        elif args.command == "setup-repo":
            out = admin.setup_repo(args.project, args.backend, args.codex_env)
        elif args.command == "protect":
            out = admin.protect(args.project, args.check)
        else:
            out = admin.activate(args.bot)
    except (Refused, InstallError, PolicyError, ContractError, yaml.YAMLError) as err:
        print(f"team-kit-admin: refused: {err}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
