from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from faker_kit_env import CLAUDE_CONFIG_DIR, parse_env_file, set_env
from faker_kit_gh import Refused
from install_config import InstallError, apply_profile, config_set, cron_jobs, cron_spec, hermes, mapping, profile_home, value_at

PLUGIN = "faker_kit_guard"
KIT_SKILL_DIRS = ("skills", "external_dirs")
ROLE_SKILL = {"project": "dispatch", "bootstrap": "bootstrap", "chief": "chief"}
SUBSCRIPTION_PLUGIN = "claude-subscription-directsdk"
SUBSCRIPTION_PROVIDER = "claude-subscription-directsdk-experimental"
SUBSCRIPTION_MIN_CLI = (2, 1, 284)
SUBSCRIPTION_DEFAULT_MODEL = "sonnet"
PLUGIN_CLAUDE_DIR_ENV = "CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR"
JOBS_PAUSED_REASON = "Awaiting the bot's GitHub login"
SCHEDULE_FIELDS = {"faker-kit-babysit": "heartbeat_every", "faker-kit-incidents": "incidents_every", "faker-kit-security": "security_every",
                   "faker-kit-issues": "heartbeat_every"}


class Profiles:
    def __init__(self, root: Path, pkg: Path) -> None:
        self.root, self.pkg = root, pkg

    def home(self, bot: str) -> Path:
        return profile_home(self.root, bot)

    def live(self, bot: str) -> bool:
        try:
            hermes("profile", "show", bot)
        except InstallError:
            return False
        return True

    def ensure(self, bot: str) -> Path:
        if not self.live(bot):
            hermes("-p", "default", "profile", "create", bot)
            if not self.live(bot):
                raise Refused(f"hermes profile create {bot} did not create a profile Hermes serves at {self.home(bot)}")
        return self.home(bot)

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

    def refresh(self, bot: str, role: str, ask_owner: bool, model: str | None, claude_config_dir: str | None) -> Path:
        home = self.ensure(bot)
        apply_profile(self.root, bot, self.pkg / "profiles" / role / "config.yaml")
        if not ask_owner:
            config_set(self.root, bot, ("approvals", "mode"), "off")
        if claude_config_dir is not None:
            self.use_subscription(bot, home, model, claude_config_dir)
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
        hermes("-p", bot, "plugins", "enable", SUBSCRIPTION_PROVIDER, "--no-allow-tool-override")
        config_set(self.root, bot, ("model", "provider"), SUBSCRIPTION_PROVIDER)
        config_set(self.root, bot, ("model", "default"), model or SUBSCRIPTION_DEFAULT_MODEL)
        current = mapping(home / "config.yaml")
        for key in ("base_url", "api_mode"):
            if value_at(current, ("model", key)) is not None:
                hermes("-p", bot, "config", "unset", f"model.{key}")
        self.record_config_dir(home, config_dir)
        if config_dir:
            set_env(self.root, PLUGIN_CLAUDE_DIR_ENV, config_dir)

    def record_config_dir(self, home: Path, config_dir: str) -> None:
        if config_dir:
            set_env(home, CLAUDE_CONFIG_DIR, config_dir)
        set_env(home, PLUGIN_CLAUDE_DIR_ENV, "")

    def github_login(self, home: Path, machine_login: bool) -> bool:
        return bool(parse_env_file(home / ".env").get("GH_TOKEN")) or machine_login

    def ensure_jobs(self, bot: str, intervals: dict[str, str], slack_channel: str | None, enabled: bool) -> list[str]:
        existing = cron_jobs(hermes("-p", bot, "cron", "list", "--all"))
        created = []
        for job in cron_spec(self.pkg / "cron" / "project-jobs.md", bot):
            schedule = f"every {intervals[SCHEDULE_FIELDS[job['name']]]}"
            deliver = f"{job['deliver']},slack:{slack_channel}" if slack_channel else job["deliver"]
            current = existing.get(job["name"])
            if current is None:
                args = ["-p", bot, "cron", "create", schedule, job["prompt"], "--name", job["name"], "--deliver", deliver, "--continuity"]
                if job["skill"]:
                    args += ["--skill", job["skill"]]
                if not enabled:
                    args += ["--paused", "--paused-reason", JOBS_PAUSED_REASON]
                hermes(*args)
                created.append(job["name"])
                continue
            hermes("-p", bot, "cron", "edit", current["id"], "--schedule", schedule, "--prompt", job["prompt"])
            if enabled and not current["enabled"]:
                hermes("-p", bot, "cron", "resume", current["id"])
        return created

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


def under(entry: str, home: Path, folder: Path) -> bool:
    path = Path(os.path.expandvars(entry)).expanduser()
    try:
        return (home / path).resolve().is_relative_to(folder)
    except (OSError, RuntimeError):
        return False
