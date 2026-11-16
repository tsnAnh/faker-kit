from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from faker_kit_cron_list import cron_jobs
from faker_kit_env import CLAUDE_CONFIG_DIR, parse_env_file, set_env
from faker_kit_gh import Refused
from faker_kit_incidents import redact
from faker_kit_machine import SUBSCRIPTION_PROVIDER
from install_config import KIT_GATE_SCRIPT, InstallError, apply_profile, config_set, cron_spec, hermes, mapping, profile_home, value_at

PLUGIN = "faker_kit_guard"
AGENT_CONTEXT_ENV = "HERMES_AGENT_TOOL_CONTEXT"
PROFILE_CHECK_SECONDS = 20
REASON_CHARS = 300
APPROVAL_DENIAL = re.compile(r"needs your approval", re.I)
TOKEN_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
ROLE_SKILL_DIR = "kit-skills"
KIT_SKILL_DIRS = ("skills", "external_dirs")
KIT_SKILL_ENABLED = ("skills", "enabled")
ROLE_SKILL = {"project": "dispatch", "bootstrap": "bootstrap", "chief": "chief"}
SUBSCRIPTION_PLUGIN = "claude-subscription-directsdk"
SUBSCRIPTION_MIN_CLI = (2, 1, 284)
SUBSCRIPTION_DEFAULT_MODEL = "sonnet"
PLUGIN_CLAUDE_DIR_ENV = "CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR"
KIT_OWNED_MARK = "\nKIT_OWNED = True\n"
GATE_LIBRARIES = (("cron/scripts", "faker_kit_gate.py"), ("cron/scripts", "faker_kit_gate_news.py"), ("lib", "faker_kit_cron_list.py"))
RETIRED_JOBS = {"project-jobs.md": ("faker-kit-issues", "faker-kit-security", "faker-kit-incidents")}
JOBS_PAUSED_REASON = "Awaiting the bot's GitHub login"
SCHEDULE_FIELDS = {"faker-kit-babysit": "heartbeat_every"}
UPDATE_STEP = ("updates", "post_update_steps", "faker-kit")
UPDATE_STEP_SECONDS = 1800


class JobChanges(NamedTuple):
    created: list[str]
    removed: list[str]


class Profiles:
    def __init__(self, root: Path, pkg: Path, shapes: re.Pattern[str], state: Path) -> None:
        self.root, self.pkg, self.shapes, self.state = root, pkg, shapes, state

    def home(self, bot: str) -> Path:
        return profile_home(self.root, bot)

    def live(self, bot: str) -> bool | None:
        try:
            hermes("profile", "show", bot, timeout=PROFILE_CHECK_SECONDS)
        except InstallError as err:
            return False if re.search(rf"Profile '{re.escape(bot)}' does not exist", err.detail) else None
        return True

    def reason(self, detail: str) -> str:
        known = [os.environ[name] for name in TOKEN_ENV if os.environ.get(name)]
        text = " ".join(redact(detail, known, self.shapes).split())
        return text if len(text) <= REASON_CHARS else "..." + text[-(REASON_CHARS - 3):]

    def check_skill_dirs(self, bot: str) -> None:
        config = self.home(bot) / "config.yaml"
        if not config.exists():
            return
        current = value_at(mapping(config), KIT_SKILL_DIRS)
        if current is not None and not isinstance(current, (str, list)):
            raise Refused(f"{bot}: skills.external_dirs must be a folder or a list of folders")
        self.enabled_skills(bot, config)

    def enabled_skills(self, bot: str, config: Path) -> list:
        current = value_at(mapping(config), KIT_SKILL_ENABLED)
        if current is not None and not isinstance(current, (str, list)):
            raise Refused(f"{bot}: skills.enabled must be a skill name or a list of skill names")
        return [current] if isinstance(current, str) else (current or [])

    def ensure(self, bot: str) -> Path:
        found = self.live(bot)
        if found is None:
            raise Refused(f"could not check whether bot {bot} has a Hermes profile, because hermes profile show did not answer; nothing was changed, so try again")
        if not found:
            try:
                hermes("-p", "default", "profile", "create", bot)
            except InstallError as err:
                raise Refused(self.creation_refusal(bot, err)) from err
            if not self.live(bot):
                raise Refused(f"hermes profile create {bot} did not create a profile Hermes serves at {self.home(bot)}")
        return self.home(bot)

    def creation_refusal(self, bot: str, err: InstallError) -> str:
        if os.environ.get(AGENT_CONTEXT_ENV) and APPROVAL_DENIAL.search(err.detail):
            return (
                f"bot {bot} has no Hermes profile, and creating one needs the owner's approval. Your next command is `hermes profile create {bot}`, "
                f"alone on the command line; the owner approves it in this chat. Then run this command again. If this turn cannot show an approval "
                f"(a relayed message or a scheduled job), reply with one line saying bot {bot} needs its profile created"
            )
        reason = self.reason(err.detail)
        return f"hermes profile create {bot} failed" + (f": {reason}" if reason else "")

    def link_skill(self, home: Path, name: str) -> None:
        dest = home / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            sys.stderr.write(f"conflict: {target} is a real directory; left as is\n")
            return
        target.symlink_to(self.pkg / "skills" / name)

    def expose_skill(self, bot: str, home: Path, name: str) -> None:
        folder = home / ROLE_SKILL_DIR
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.iterdir():
            if stale.name != name and stale.is_symlink():
                stale.unlink()
        target = folder / name
        if target.is_symlink() and target.readlink() != self.pkg / "skills" / name:
            target.unlink()
        if target.exists() and not target.is_symlink():
            sys.stderr.write(f"conflict: {target} is a real directory; left as is\n")
        elif not target.is_symlink():
            target.symlink_to(self.pkg / "skills" / name)
        current = value_at(mapping(home / "config.yaml"), KIT_SKILL_DIRS)
        listed = [current] if isinstance(current, str) else current
        if listed is not None and not isinstance(listed, list):
            raise Refused(f"{bot}: skills.external_dirs must be a folder or a list of folders")
        if str(folder) not in (listed or []):
            config_set(self.root, bot, KIT_SKILL_DIRS, [*(listed or []), str(folder)])
        enabled = self.enabled_skills(bot, home / "config.yaml")
        if name not in enabled:
            config_set(self.root, bot, KIT_SKILL_ENABLED, [*enabled, name])

    def refresh(self, bot: str, role: str, ask_owner: bool, model: str | None, claude_config_dir: str | None, provider: str | None = None) -> Path:
        home = self.ensure(bot)
        apply_profile(self.root, bot, self.pkg / "profiles" / role / "config.yaml", {} if ask_owner else {("approvals", "mode"): "off"})
        if claude_config_dir is not None:
            self.use_subscription(bot, home, model, claude_config_dir)
        elif provider:
            config_set(self.root, bot, ("model", "provider"), provider)
            config_set(self.root, bot, ("model", "default"), str(model))
            self.unset_endpoint(bot, home)
        elif model:
            config_set(self.root, bot, ("model", "default"), model)
        soul = (self.pkg / "profiles" / role / "SOUL.md").read_text().rstrip() + "\n\n" + (self.pkg / "profiles" / "OPERATING.md").read_text()
        if (home / "SOUL.md").exists() and (home / "SOUL.md").read_text() != soul:
            shutil.copy2(home / "SOUL.md", home / "SOUL.md.bak")
        (home / "SOUL.md").write_text(soul)
        plugin = home / "plugins" / PLUGIN
        shutil.rmtree(plugin, ignore_errors=True)
        shutil.copytree(self.pkg / "plugin" / PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__"))
        self.link_skill(home, ROLE_SKILL[role])
        self.expose_skill(bot, home, ROLE_SKILL[role])
        return home

    def subscription_in_use(self, bot: str) -> bool:
        return value_at(mapping(self.home(bot) / "config.yaml"), ("model", "provider")) == SUBSCRIPTION_PROVIDER

    def claude_login(self) -> str:
        claude = shutil.which("claude")
        if not claude:
            raise Refused("Claude Code is not installed: npm install -g @anthropic-ai/claude-code, then claude auth login")
        try:
            version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=30).stdout
            status = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("Claude Code did not answer --version or auth status") from None
        found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", version)
        if not found or tuple(int(n) for n in found.groups()) < SUBSCRIPTION_MIN_CLI:
            raise Refused(f"Claude Code {'.'.join(map(str, SUBSCRIPTION_MIN_CLI))} or later is needed; update it with npm install -g @anthropic-ai/claude-code")
        try:
            logged_in = json.loads(status).get("loggedIn") is True
        except (ValueError, AttributeError):
            logged_in = False
        if not logged_in:
            raise Refused("Claude Code is not logged in here: run claude auth login, then run this again")
        return os.environ.get(CLAUDE_CONFIG_DIR, "")

    def use_subscription(self, bot: str, home: Path, model: str | None, config_dir: str) -> None:
        if not (home / "plugins" / SUBSCRIPTION_PROVIDER).is_dir():
            hermes("-p", bot, "plugins", "install", SUBSCRIPTION_PLUGIN)
        if not self.subscription_enabled(home):
            hermes("-p", bot, "plugins", "enable", SUBSCRIPTION_PROVIDER, "--no-allow-tool-override")
        config_set(self.root, bot, ("model", "provider"), SUBSCRIPTION_PROVIDER)
        config_set(self.root, bot, ("model", "default"), model or SUBSCRIPTION_DEFAULT_MODEL)
        self.unset_endpoint(bot, home)
        self.record_config_dir(home, config_dir)
        if config_dir:
            set_env(self.root, PLUGIN_CLAUDE_DIR_ENV, config_dir)

    def subscription_enabled(self, home: Path) -> bool:
        config = mapping(home / "config.yaml")
        enabled = value_at(config, ("plugins", "enabled"))
        return (isinstance(enabled, list) and SUBSCRIPTION_PROVIDER in enabled and not value_at(config, ("plugins", "disabled"))
                and value_at(config, ("plugins", "entries", SUBSCRIPTION_PROVIDER, "allow_tool_override")) is False
                and not declares_capabilities(home / "plugins" / SUBSCRIPTION_PROVIDER))

    def unset_endpoint(self, bot: str, home: Path) -> None:
        current = mapping(home / "config.yaml")
        for key in ("base_url", "api_mode"):
            if value_at(current, ("model", key)) is not None:
                hermes("-p", bot, "config", "unset", f"model.{key}")

    def record_config_dir(self, home: Path, config_dir: str) -> None:
        if config_dir:
            set_env(home, CLAUDE_CONFIG_DIR, config_dir)
        set_env(home, PLUGIN_CLAUDE_DIR_ENV, "")

    def github_login(self, home: Path, machine_login: bool) -> bool:
        return bool(parse_env_file(home / ".env").get("GH_TOKEN")) or machine_login

    def ensure_jobs(self, bot: str, spec: str, intervals: dict[str, str], slack_channel: str | None, enabled: bool, chief: str | None = None) -> JobChanges:
        existing = cron_jobs(hermes("-p", bot, "cron", "list", "--all"))
        jobs = cron_spec(self.pkg / "cron" / spec, bot, chief)
        scripts = {job["name"]: self.job_script(bot, self.pkg / "cron" / "scripts" / job["script"]) for job in jobs if job["script"]}
        libraries = [self.job_script(bot, self.pkg / folder / name) for folder, name in GATE_LIBRARIES] if scripts else []
        records = self.job_records(bot)
        digests = {}
        created = []
        edited = False
        for library in libraries:
            self.write_job_script(*library)
        for job in jobs:
            schedule = f"every {intervals[SCHEDULE_FIELDS[job['name']]]}" if intervals and job["name"] in SCHEDULE_FIELDS else job["schedule"]
            deliver = f"{job['deliver']},slack:{slack_channel}" if slack_channel and not job["chief"] else job["deliver"]
            current = existing.get(job["name"])
            script_args = ["--script", self.write_job_script(*scripts[job["name"]])] if job["script"] else []
            digests[job["name"]] = job_digest(schedule, job["prompt"], job["script"]) if not job["chief"] else job_digest(schedule, job["prompt"], job["script"], deliver)
            if current is None:
                args = ["-p", bot, "cron", "create", schedule, job["prompt"], "--name", job["name"], "--deliver", deliver, "--continuity"]
                if job["skill"]:
                    args += ["--skill", job["skill"]]
                args += script_args + (["--no-agent"] if job["no_agent"] else [])
                if not enabled:
                    args += ["--paused", "--paused-reason", JOBS_PAUSED_REASON]
                hermes(*args)
                created.append(job["name"])
                continue
            if not job["script"] and KIT_GATE_SCRIPT.match(Path(str(current.get("script") or "")).name):
                script_args = ["--script", ""]
            if not applied(records.get(job["name"]), current, digests[job["name"]]):
                hermes("-p", bot, "cron", "edit", current["id"], "--schedule", schedule, "--prompt", job["prompt"], *script_args,
                       *(["--deliver", deliver] if job["chief"] else []))
                edited = True
            if enabled and not current["enabled"]:
                hermes("-p", bot, "cron", "resume", current["id"])
        removed = self.retire_jobs(bot, spec, existing, {target.name for target, _ in scripts.values()})
        if created or edited or removed:
            self.record_jobs(bot, digests)
        return JobChanges(created, removed)

    def retire_jobs(self, bot: str, spec: str, existing: dict[str, dict], kept_scripts: set[str]) -> list[str]:
        removed = []
        for name in RETIRED_JOBS.get(spec, ()):
            current = existing.get(name)
            if current is not None:
                hermes("-p", bot, "cron", "remove", current["id"])
                removed.append(name)
            named = {f"{name}-gate.py", Path(str((current or {}).get("script") or "")).name} - kept_scripts
            for script in sorted(n for n in named if KIT_GATE_SCRIPT.match(n)):
                self.drop_kit_script(self.home(bot) / "scripts" / script)
            (self.state / "gates" / bot / f"{name}.json").unlink(missing_ok=True)
        return removed

    @staticmethod
    def drop_kit_script(target: Path) -> None:
        if target.is_file() and not target.is_symlink() and KIT_OWNED_MARK.encode() in target.read_bytes():
            target.unlink()

    def job_records(self, bot: str) -> dict[str, dict]:
        try:
            records = json.loads((self.state / "jobs" / f"{bot}.json").read_text())
        except (OSError, ValueError):
            return {}
        return {name: record for name, record in records.items() if isinstance(record, dict)} if isinstance(records, dict) else {}

    def record_jobs(self, bot: str, digests: dict[str, str]) -> None:
        listed = cron_jobs(hermes("-p", bot, "cron", "list", "--all"))
        records = {name: {"id": listed[name]["id"], "digest": digest, "schedule": listed[name].get("schedule"), "script": listed[name].get("script")}
                   for name, digest in digests.items() if name in listed}
        target = self.state / "jobs" / f"{bot}.json"
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{os.getpid()}")
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            out.write(json.dumps(records, sort_keys=True))
        temp.replace(target)

    def job_script(self, bot: str, source: Path) -> tuple[Path, str]:
        name = source.name
        if not source.is_file():
            raise InstallError(f"cron script {name} is not in the kit at {source}")
        text = source.read_text()
        for placeholder, value in (("@WRAPPER@", str(self.root / "bin" / "faker-kit")), ("@HERMES_HOME@", str(self.home(bot))),
                                   ("@FAKER_KIT_HOME@", str(self.state)), ("@PATH@", os.environ.get("PATH", ""))):
            text = text.replace(f'"{placeholder}"', json.dumps(value))
        target = self.home(bot) / "scripts" / name
        if target.is_symlink() or (target.exists() and not target.is_file()) or (target.is_file() and KIT_OWNED_MARK.encode() not in target.read_bytes()):
            raise Refused(f"{target} was not written by the kit; move it aside, then run the install again")
        return target, text

    def write_job_script(self, target: Path, text: str) -> str:
        if not target.is_file() or target.read_text() != text:
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f"{target.name}.tmp.{os.getpid()}")
            temp.write_text(text)
            temp.chmod(0o600)
            temp.replace(target)
        return target.name

    def register_update_step(self) -> None:
        config_set(self.root, "default", (*UPDATE_STEP, "command"), [str(self.root / "bin" / "faker-kit"), "refresh"])
        config_set(self.root, "default", (*UPDATE_STEP, "timeout"), UPDATE_STEP_SECONDS)

    def kit_skills(self, profiles: list[str], plugins_dir: Path, wanted: list[str]) -> list[str]:
        plugins = plugins_dir.resolve()
        covered = []
        for profile in dict.fromkeys(profiles):
            home = self.home(profile)
            if not home.is_dir():
                continue
            current = value_at(mapping(home / "config.yaml"), KIT_SKILL_DIRS)
            if isinstance(current, str):
                current = [current]
            if current is not None and not isinstance(current, list):
                raise Refused(f"{profile}: skills.external_dirs must be a folder or a list of folders")
            kept = [d for d in current or [] if not (isinstance(d, str) and under(d, home, plugins))]
            if kept + wanted != (current or []):
                config_set(self.root, profile, KIT_SKILL_DIRS, kept + wanted)
            covered.append(profile)
        return covered


def job_digest(schedule: str, prompt: str, script: str | None, *deliver: str) -> str:
    return hashlib.sha256(json.dumps([schedule, prompt, script, *deliver]).encode()).hexdigest()


def applied(record: dict | None, current: dict, digest: str) -> bool:
    return (record is not None and record.get("id") == current["id"] and record.get("digest") == digest
            and record.get("schedule") == current.get("schedule") and record.get("script") == current.get("script"))


def declares_capabilities(plugin: Path) -> bool:
    manifest = next((plugin / name for name in ("plugin.yaml", "plugin.yml") if (plugin / name).is_file()), None)
    if manifest is None:
        return True
    try:
        declared = mapping(manifest).get("capabilities")
    except InstallError:
        return True
    return isinstance(declared, list) and any(isinstance(item, str) for item in declared)


def under(entry: str, home: Path, folder: Path) -> bool:
    path = Path(os.path.expandvars(entry)).expanduser()
    try:
        return (home / path).resolve().is_relative_to(folder)
    except (OSError, RuntimeError):
        return False
