from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from faker_kit_gh import Refused
from faker_kit_guard.t3code import LAUNCH_TOOL, RECORD_KEEP_SECONDS, RECORD_VERSION, launches_dir, locked, message_digest, record_path, write_private
from faker_kit_launch import DISPATCH_FILE, Dispatch, LaunchResult

SECTION = ".agents/dispatch.json t3code"
SETTINGS = ("project", "base_ref", "provider", "model", "max_in_flight")
PROJECT_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
PROJECT_PATH = re.compile(r"\A/[^\x00-\x1f\x7f]{1,4095}\Z")
REF = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
SLUG = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}\Z")
UNIT_CHARS = re.compile(r"[^a-z0-9-]+")
UNIT_MAX_CHARS = 64
DEFAULT_MAX_IN_FLIGHT = 2
MAX_IN_FLIGHT_LIMIT = 20
ORIGIN_PREFIX = "origin/"
DEFAULT_PROVIDER = "claudeAgent"
DEFAULT_MODEL = "claude-sonnet-5-5"
BACKEND = "t3code"
SEEDED_PROJECTS = {
    "titoniumlab/titox-mobile-sdk": "03f86665-683a-41b2-9982-f56069044a53",
}


@dataclass(frozen=True)
class T3Settings:
    project: str
    base_ref: str
    provider: str | None
    model: str | None
    max_in_flight: int

    @property
    def project_is_path(self) -> bool:
        return self.project.startswith("/")

    @property
    def selection(self) -> tuple[str, str]:
        return (self.provider, self.model) if self.provider and self.model else (DEFAULT_PROVIDER, DEFAULT_MODEL)


def text_setting(section: dict[str, object], key: str, shape: re.Pattern[str]) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not shape.match(value) or (key == "base_ref" and ".." in value):
        raise Refused(f"{SECTION}.{key} is not a valid value")
    return value


def parse_settings(section: object, default_branch: str) -> T3Settings:
    if not isinstance(section, dict):
        raise Refused(f'{SECTION} is missing: set {{"project": "<T3 project id or absolute path on the Mac>"}} in .agents/dispatch.json to dispatch to T3 Code')
    unknown = sorted(str(key) for key in section if key not in SETTINGS)
    if unknown:
        raise Refused(f"{SECTION} may hold only {', '.join(SETTINGS)}, not {', '.join(unknown)}")
    project = section.get("project")
    if not isinstance(project, str) or not (PROJECT_ID.match(project) or PROJECT_PATH.match(project)):
        raise Refused(f"{SECTION}.project must be a T3 project id or an absolute path on the Mac")
    provider = text_setting(section, "provider", SLUG)
    model = text_setting(section, "model", SLUG)
    if (provider is None) != (model is None):
        raise Refused(f"{SECTION}.provider and {SECTION}.model go together")
    limit = section.get("max_in_flight", DEFAULT_MAX_IN_FLIGHT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_IN_FLIGHT_LIMIT:
        raise Refused(f"{SECTION}.max_in_flight must be a whole number from 1 to {MAX_IN_FLIGHT_LIMIT}")
    base = text_setting(section, "base_ref", REF) or f"{ORIGIN_PREFIX}{default_branch}"
    return T3Settings(project, base, provider, model, limit)


def with_mapped_project(section: object, repo: str, machine_projects: Mapping[str, str]) -> object:
    if isinstance(section, dict) and "project" in section:
        return section
    project = {**SEEDED_PROJECTS, **machine_projects}.get(repo.lower())
    if project is None:
        raise Refused(f"no T3 project for {repo}: add it under t3code_projects in machine.yaml")
    return {**section, "project": project} if isinstance(section, dict) else {"project": project}


def check_t3code_backend(dispatch: Dispatch, brief_name: str) -> None:
    try:
        parse_settings(dispatch.t3code, dispatch.default_branch)
        unit_id(brief_name)
    except Refused as err:
        raise Refused(f"launch is blocked: the t3code project does not resolve ({err}); fix the t3code settings in {DISPATCH_FILE}") from None


def unit_id(brief_name: str) -> str:
    stem = brief_name.removesuffix(".md")
    unit = UNIT_CHARS.sub("-", stem.lower()).strip("-")[:UNIT_MAX_CHARS].strip("-")
    if not unit:
        raise Refused("name the brief file <unit-id>.md with lowercase letters, digits, and hyphens: the unit id titles the T3 thread")
    return unit


class T3CodeLauncher:
    def __init__(self, settings: T3Settings, home: Path, bot: str, unit: str, now: Callable[[], float]) -> None:
        self.settings, self.home, self.bot, self.unit, self.now = settings, home, bot, unit, now

    def prepared_args(self) -> dict[str, object]:
        base = self.settings.base_ref
        strategy: dict[str, object] = {"type": "worktree", "baseRef": base}
        if base.startswith(ORIGIN_PREFIX):
            strategy["startFromOrigin"] = True
        args: dict[str, object] = {"title": f"Unit: {self.unit}", "workspaceStrategy": strategy}
        if not self.settings.project_is_path:
            args["projectId"] = self.settings.project
        provider, model = self.settings.selection
        args["modelSelection"] = {"instanceId": provider, "model": model}
        return args

    def record(self, repo: str, digest: str, prepared: dict[str, object]) -> None:
        now = self.now()
        directory = launches_dir(self.home, self.bot)
        try:
            directory.parent.mkdir(mode=0o700, exist_ok=True)
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
            with locked(directory):
                self.prune(directory, now)
                write_private(record_path(self.home, self.bot, digest), {
                    "version": RECORD_VERSION, "prompt_sha256": digest, "bot": self.bot, "repo": repo, "unit": self.unit, "created": now,
                    "used": False, "project_is_path": self.settings.project_is_path, "call": prepared,
                })
        except OSError as err:
            raise Refused(f"could not record the prepared launch ({err.__class__.__name__}); nothing starts") from None

    @staticmethod
    def prune(directory: Path, now: float) -> None:
        for entry in directory.glob("*.json"):
            try:
                if entry.stat().st_mtime < now - RECORD_KEEP_SECONDS:
                    entry.unlink()
            except OSError:
                continue

    def start(self, repo: str, prompt: str, _workdir: Path, model: str | None, effort: str | None, /) -> LaunchResult:
        prepared = self.prepared_args()
        self.record(repo, message_digest(prompt), prepared)
        fields: dict[str, object] = {"unit": self.unit, "call": {"tool": LAUNCH_TOOL, "args": {**prepared, "message": prompt}}, "max_in_flight": self.settings.max_in_flight}
        if self.settings.project_is_path:
            fields["resolve_project"] = {
                "tool": "mcp__t3code__t3_project_list", "workspaceRoot": self.settings.project,
                "then": "add the id of the project with that workspaceRoot to call.args as projectId",
            }
        chosen = "/".join(self.settings.selection)
        fields["model" if self.settings.provider else "model_defaulted"] = chosen
        given = {name: value for name, value in (("model", model), ("effort", effort)) if value is not None}
        if given:
            fields["flags_not_applied"] = given
        return LaunchResult(fields)
