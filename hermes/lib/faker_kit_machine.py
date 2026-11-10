from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import yaml

NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
REPO = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
LOGIN = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
EVERY = re.compile(r"\A[1-9][0-9]*[mh]\Z")
HTTPS_URL = re.compile(r"\Ahttps://[a-z0-9.-]+\.[a-z]{2,}\Z")
SLACK_CHANNEL = re.compile(r"\A[CG][A-Z0-9]{8,}\Z")
PLACEHOLDER = "CHANGE-ME"
SUBSCRIPTION_PROVIDER = "claude-subscription-directsdk-experimental"

WEBHOOK_HOST = "127.0.0.1"
WEBHOOK_PORT = 8644
ROUTE_JOBS: dict[str, tuple[str, tuple[str, ...], str, tuple[dict[str, str], ...]]] = {
    "pr": ("faker-kit-babysit", ("pull_request", "pull_request_review", "check_suite", "status"),
           "GitHub event {event_type} ({action}) in {repository.full_name}. Pull request {pull_request.number}, "
           "check suite head {check_suite.head_sha}. Babysit the open pull requests.", ()),
    "issues": ("faker-kit-babysit", ("issues",),
               "GitHub event issues ({action}) in {repository.full_name}: the faker:fix label was added to issue {issue.number}. "
               "Follow the Bug reports section of the dispatch skill.",
               ({"field": "action", "equals": "labeled"}, {"field": "label.name", "equals": "faker:fix"})),
}


class Route(TypedDict):
    events: list[str]
    secret: str
    profile: str
    cron_job: str
    deliver: str
    prompt: str
    filters: list[dict[str, str]]


class MachineError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    bot: str
    project: str
    repo: str
    heartbeat_every: str | None = None
    model: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class CiRunner:
    repo: str
    slots: int = 1


@dataclass(frozen=True)
class Slack:
    bot: str
    channel_id: str


@dataclass(frozen=True)
class Machine:
    bootstrap_bot: str
    trusted_authors: tuple[str, ...]
    ask_owner: bool
    max_in_flight_total: int
    claude_subscription: bool
    projects: tuple[Project, ...]
    chief: str | None = None
    chief_model: str | None = None
    chief_provider: str | None = None
    bootstrap_model: str | None = None
    bootstrap_provider: str | None = None
    webhook_url: str | None = None
    tunnel: str | None = None
    slack: Slack | None = None
    kit_ref: str | None = None
    skills: tuple[str, ...] = ()
    ci_runners: tuple[CiRunner, ...] = ()


def _mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MachineError(f"{where} must be a mapping")
    return {str(k): v for k, v in value.items()}


def _text(data: dict[str, object], key: str, where: str, pattern: re.Pattern[str] | None = None, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise MachineError(f"{where}.{key} must be a non-empty string")
    if PLACEHOLDER in value:
        raise MachineError(f"{where}.{key} is still {value!r}; fill it in first")
    if pattern is not None and not pattern.match(value):
        raise MachineError(f"{where}.{key} {value!r} is not valid")
    return value


def check_model_choice(provider: str | None, model: str | None, subscription: bool, where: str) -> None:
    if provider is None:
        return
    if PLACEHOLDER in provider or not NAME.match(provider):
        raise MachineError(f"{where}: provider {provider!r} is not a Hermes provider id")
    if provider == SUBSCRIPTION_PROVIDER:
        raise MachineError(f"{where}: use claude_subscription (--claude-subscription) for the Claude subscription provider, not provider")
    if not model:
        raise MachineError(f"{where}: provider {provider} needs a model too; give the provider and its model together")
    if subscription:
        raise MachineError(f"{where}: provider {provider} conflicts with claude_subscription (--claude-subscription); choose one for this bot")


def _model_choice(data: dict[str, object], model_key: str, provider_key: str, where: str, subscription: bool) -> tuple[str | None, str | None]:
    model = _text(data, model_key, where, required=False)
    provider = _text(data, provider_key, where, required=False)
    check_model_choice(provider, model, subscription, f"{where}.{provider_key}")
    return model, provider


def parse_machine(data: object) -> Machine:
    top = _mapping(data, "machine config")
    if top.get("version") != 1:
        raise MachineError("machine config version must be 1")
    guard = _mapping(top.get("guard") or {}, "guard")
    authors = guard.get("trusted_authors")
    if not isinstance(authors, list) or not authors or not all(isinstance(a, str) and LOGIN.match(a) for a in authors):
        raise MachineError("guard.trusted_authors must list GitHub logins")
    if PLACEHOLDER in authors:
        raise MachineError("guard.trusted_authors still lists CHANGE-ME; fill it in first")
    ask_owner = guard.get("ask_owner", True)
    total = guard.get("max_in_flight_total", 3)
    if not isinstance(ask_owner, bool):
        raise MachineError("guard.ask_owner must be true or false")
    if not isinstance(total, int) or isinstance(total, bool) or not 1 <= total <= 50:
        raise MachineError("guard.max_in_flight_total must be a number from 1 to 50")
    subscription = top.get("claude_subscription", False)
    if not isinstance(subscription, bool):
        raise MachineError("claude_subscription must be true or false")

    raw_projects = top.get("projects")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise MachineError("projects must list at least one project")
    projects = []
    for i, item in enumerate(raw_projects):
        where = f"projects[{i}]"
        entry = _mapping(item, where)
        heartbeat_every = _text(entry, "heartbeat_every", where, EVERY, required=False)
        model, provider = _model_choice(entry, "model", "provider", where, subscription)
        projects.append(Project(bot=str(_text(entry, "bot", where, NAME)), project=str(_text(entry, "project", where, NAME)),
                                repo=str(_text(entry, "repo", where, REPO)), heartbeat_every=heartbeat_every, model=model, provider=provider))
    bots = [p.bot for p in projects]
    bootstrap = str(_text(top, "bootstrap_bot", "machine config", NAME))
    bootstrap_model, bootstrap_provider = _model_choice(top, "bootstrap_model", "bootstrap_provider", "machine config", subscription)
    chief = _mapping(top.get("chief") or {}, "chief")
    chief_bot = _text(chief, "bot", "chief", NAME, required=False)
    chief_model, chief_provider = _model_choice(chief, "model", "provider", "chief", subscription)
    names = [*bots, bootstrap, *([chief_bot] if chief_bot else [])]
    if len(set(names)) != len(names):
        raise MachineError("every bot needs its own profile name")
    if len({p.repo.lower() for p in projects}) != len(projects):
        raise MachineError("a repository belongs to one project")
    if len({secret_env(b) for b in bots}) != len(bots):
        raise MachineError("two project bots differ only in - and _, so they would share a webhook secret; rename one")

    webhooks = _mapping(top.get("webhooks") or {}, "webhooks")
    url = _text(webhooks, "url", "webhooks", HTTPS_URL, required=False)
    tunnel = _text(webhooks, "tunnel", "webhooks", NAME, required=False)
    if bool(url) != bool(tunnel):
        raise MachineError("webhooks needs both url and tunnel, or neither")

    slack = None
    if top.get("slack") is not None:
        raw = _mapping(top["slack"], "slack")
        slack = Slack(bot=str(_text(raw, "bot", "slack", NAME)), channel_id=str(_text(raw, "channel_id", "slack", SLACK_CHANNEL)))
        if slack.bot not in bots:
            raise MachineError(f"slack.bot {slack.bot} must be one of the project bots")
    kit_ref = _text(top, "kit_ref", "machine config", re.compile(r"\A[0-9a-f]{40}\Z"), required=False)
    skills = [] if top.get("skills") is None else top["skills"]
    if not isinstance(skills, list) or not all(isinstance(s, str) and NAME.match(s) for s in skills):
        raise MachineError("skills must list kit plugin names, such as pstack")
    raw_runners = [] if top.get("ci_runners") is None else top["ci_runners"]
    if not isinstance(raw_runners, list):
        raise MachineError("ci_runners must list repositories")
    runners = []
    for i, item in enumerate(raw_runners):
        where = f"ci_runners[{i}]"
        entry = _mapping(item, where)
        slots = entry.get("slots", 1)
        if not isinstance(slots, int) or isinstance(slots, bool) or not 1 <= slots <= 8:
            raise MachineError(f"{where}.slots must be a number from 1 to 8")
        runners.append(CiRunner(repo=str(_text(entry, "repo", where, REPO)), slots=slots))
    if len({r.repo.lower() for r in runners}) != len(runners):
        raise MachineError("ci_runners lists a repository twice")
    return Machine(bootstrap_bot=bootstrap, trusted_authors=tuple(authors), ask_owner=ask_owner, max_in_flight_total=total,
                   claude_subscription=subscription, projects=tuple(projects), chief=chief_bot,
                   chief_model=chief_model, chief_provider=chief_provider, bootstrap_model=bootstrap_model,
                   bootstrap_provider=bootstrap_provider, webhook_url=url, tunnel=tunnel, slack=slack, kit_ref=kit_ref,
                   skills=tuple(dict.fromkeys(skills)), ci_runners=tuple(runners))


def load_machine(path: Path) -> Machine:
    try:
        return parse_machine(yaml.safe_load(path.read_text()))
    except OSError as err:
        raise MachineError(f"cannot read machine config {path}: {err.strerror}") from None


def secret_env(bot: str) -> str:
    return "FAKER_KIT_WEBHOOK_SECRET_" + re.sub(r"[^A-Z0-9]", "_", bot.upper())


def routes(bot: str) -> dict[str, Route]:
    return {f"{bot}-{kind}": Route(events=list(events), secret="${" + secret_env(bot) + "}", profile=bot, cron_job=job, deliver="log", prompt=prompt,
                                   filters=[dict(f) for f in filters])
            for kind, (job, events, prompt, filters) in ROUTE_JOBS.items()}


def route_url(base: str, bot: str, route: str) -> str:
    return f"{base}/p/{bot}/webhooks/{route}"


def stale_route(name: str, bot: str) -> bool:
    kind = re.fullmatch(re.escape(bot) + r"-([a-z]+)", name)
    return kind is not None and kind.group(1) not in ROUTE_JOBS


def stale_hook(url: str, base: str, bot: str) -> bool:
    return stale_route(url.removeprefix(route_url(base, bot, "")), bot) if url.startswith(route_url(base, bot, "")) else False


def cloudflared_config(tunnel_id: str, credentials: Path, hostname: str, machine: Machine) -> str:
    rules = "".join(f"  - hostname: {hostname}\n    path: ^{re.escape(f'/p/{project.bot}/webhooks/')}[a-z0-9_-]+$\n"
                    f"    service: http://{WEBHOOK_HOST}:{WEBHOOK_PORT}\n"
                    for project in machine.projects)
    return f"tunnel: {tunnel_id}\ncredentials-file: {credentials}\n\ningress:\n{rules}  - service: http_status:404\n"


GUARD_SCALARS = ("max_in_flight_total", "ask_owner")


def edit_guard(text: str, machine: Machine) -> str:
    def scalar(value: object) -> str:
        return ("true" if value else "false") if isinstance(value, bool) else str(value)

    def replace(pattern: str, new: str, source: str, what: str) -> str:
        out, count = re.subn(pattern, lambda _: new, source, count=1, flags=re.M)
        if count != 1:
            raise MachineError(f"guard.yaml has no {what} line to set")
        return out

    for key in GUARD_SCALARS:
        text = replace(rf"^{key}:.*$", f"{key}: {scalar(getattr(machine, key))}", text, key)
    authors = "trusted_authors:\n" + "".join(f"  - {a}\n" for a in machine.trusted_authors)
    return replace(r"^trusted_authors:\n(?:[ \t]+-[^\n]*\n|[ \t]*#[^\n]*\n)*", authors, text, "trusted_authors")
