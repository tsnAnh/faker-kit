"""The machine config: one file that describes a whole Hermes machine, so hermes/setup-machine.sh can
rebuild it anywhere. It names the bots, the guard settings, the public webhook address, and Slack, and
holds no secret: tokens and webhook secrets stay in the profiles' .env files.
"""

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

WEBHOOK_HOST = "127.0.0.1"
WEBHOOK_PORT = 8644
# Each route fires one of the bot's no-agent jobs by name, so a route survives the job's id changing.
ROUTE_JOBS = {"tick": ("team-kit-tick", ("issues", "issue_comment", "pull_request", "check_suite")),
              "security": ("team-kit-security", ("push",))}


class Route(TypedDict):
    events: list[str]
    secret: str
    profile: str
    cron_job: str
    deliver: str


class MachineError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    bot: str
    project: str
    repo: str
    tick_every: str | None = None
    incidents_every: str | None = None
    security_every: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class Slack:
    bot: str
    channel_id: str
    channel_name: str


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
    webhook_url: str | None = None
    tunnel: str | None = None
    slack: Slack | None = None
    kit_ref: str | None = None


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
        every = {k: _text(entry, k, where, EVERY, required=False) for k in ("tick_every", "incidents_every", "security_every")}
        projects.append(Project(bot=str(_text(entry, "bot", where, NAME)), project=str(_text(entry, "project", where, NAME)),
                                repo=str(_text(entry, "repo", where, REPO)), model=_text(entry, "model", where, required=False), **every))
    bots = [p.bot for p in projects]
    bootstrap = str(_text(top, "bootstrap_bot", "machine config", NAME))
    chief = _mapping(top.get("chief") or {}, "chief")
    chief_bot = _text(chief, "bot", "chief", NAME, required=False)
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
        slack = Slack(bot=str(_text(raw, "bot", "slack", NAME)), channel_id=str(_text(raw, "channel_id", "slack", SLACK_CHANNEL)),
                      channel_name=str(_text(raw, "channel_name", "slack", NAME)))
        if slack.bot not in bots:
            raise MachineError(f"slack.bot {slack.bot} must be one of the project bots")
    kit_ref = _text(top, "kit_ref", "machine config", re.compile(r"\A[0-9a-f]{40}\Z"), required=False)
    return Machine(bootstrap_bot=bootstrap, trusted_authors=tuple(authors), ask_owner=ask_owner, max_in_flight_total=total,
                   claude_subscription=subscription, projects=tuple(projects), chief=chief_bot,
                   chief_model=_text(chief, "model", "chief", required=False), webhook_url=url, tunnel=tunnel, slack=slack, kit_ref=kit_ref)


def load_machine(path: Path) -> Machine:
    try:
        return parse_machine(yaml.safe_load(path.read_text()))
    except OSError as err:
        raise MachineError(f"cannot read machine config {path}: {err.strerror}") from None


def secret_env(bot: str) -> str:
    """The root .env variable that holds a bot's webhook secret."""
    return "TEAM_KIT_WEBHOOK_SECRET_" + re.sub(r"[^A-Z0-9]", "_", bot.upper())


def routes(bot: str) -> dict[str, Route]:
    """The Hermes webhook routes for one project bot, keyed by route name. The secret is a ${VAR} reference
    that Hermes expands from the root .env, so config.yaml never holds it."""
    return {f"{bot}-{kind}": Route(events=list(events), secret="${" + secret_env(bot) + "}", profile=bot, cron_job=job, deliver="log")
            for kind, (job, events) in ROUTE_JOBS.items()}


def route_url(base: str, bot: str, route: str) -> str:
    return f"{base}/p/{bot}/webhooks/{route}"


def cloudflared_config(tunnel_id: str, credentials: Path, hostname: str, machine: Machine) -> str:
    """Only this machine's own signed routes are public, each by its exact path. Any other path, another
    route on the same listener or the gateway's /health included, is 404, so a route added later is never
    published by accident."""
    rules = "".join(f"  - hostname: {hostname}\n    path: ^{re.escape(f'/p/{project.bot}/webhooks/{name}')}$\n"
                    f"    service: http://{WEBHOOK_HOST}:{WEBHOOK_PORT}\n"
                    for project in machine.projects for name in routes(project.bot))
    return f"tunnel: {tunnel_id}\ncredentials-file: {credentials}\n\ningress:\n{rules}  - service: http_status:404\n"


GUARD_SCALARS = ("max_in_flight_total", "ask_owner")
SLACK_KEYS = ("enabled", "bot", "channel_id", "channel_name")


def edit_guard(text: str, machine: Machine) -> str:
    """Set the machine's values in guard.yaml in place, keeping its comments. Every key must already be there,
    as the template writes it, so a key never ends up twice."""
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
    text = replace(r"^trusted_authors:\n(?:[ \t]+-[^\n]*\n|[ \t]*#[^\n]*\n)*", authors, text, "trusted_authors")
    block = re.search(r"^slack:\n((?:[ \t]+[^\n]*\n|[ \t]*\n)*)", text, re.M)
    if block is None:
        raise MachineError("guard.yaml has no slack block to set")
    body = block.group(1)
    values: dict[str, object] = {"enabled": machine.slack is not None}
    if machine.slack is not None:
        values.update(bot=machine.slack.bot, channel_id=machine.slack.channel_id, channel_name=machine.slack.channel_name)
    for key, value in values.items():
        indent = re.search(rf"^([ \t]+){key}:", body, re.M)
        body = replace(rf"^[ \t]+{key}:.*$", f"{indent.group(1) if indent else '  '}{key}: {scalar(value)}", body, f"slack.{key}")
    return text[:block.start(1)] + body + text[block.end(1):]
