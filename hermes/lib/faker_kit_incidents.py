from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from faker_kit_gh import Gh, Refused
from faker_kit_incident_sources import HOST_NAME, ExternalObservation, FetchJson, IncidentSourceError, https_get, poll_sources, validate_sources

HEALTH_STREAK = 2
HEALTH_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
INCIDENT_KEY = re.compile(r"\A[a-z0-9][a-z0-9:_-]{0,80}\Z")
FAILED_CONCLUSIONS = ("failure", "error", "cancelled", "timed_out", "action_required", "startup_failure")
NOT_STARTED = "The job was not started because"
SETTINGS = {"main_checks", "health", "sources", "andon_on_red"}
MAIN_KEY = "main"
MAX_REDIRECTS = 3
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
REDACTED = "[redacted]"

FetchStatus = Callable[[str], "int | None"]


class IncidentError(Exception):
    pass


@dataclass(frozen=True)
class Health:
    name: str
    url: str


@dataclass(frozen=True)
class Settings:
    main_checks: bool
    health: tuple[Health, ...]
    sources: list[dict[str, object]]
    andon_on_red: bool


@dataclass(frozen=True)
class Observation:
    key: str
    title: str
    failing: bool | None
    needed: int
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class Report:
    new: list[dict[str, object]]
    resolved: list[dict[str, object]]


def settings(config_text: str | None, alert_hosts: frozenset[str]) -> Settings:
    if config_text is None:
        return Settings(True, (), [], False)
    try:
        config = json.loads(config_text)
    except ValueError:
        raise IncidentError(".agents/dispatch.json is not valid JSON") from None
    section = config.get("incidents", {}) if isinstance(config, dict) else {}
    if not isinstance(section, dict) or not set(section) <= SETTINGS:
        raise IncidentError(".agents/dispatch.json incidents may hold only main_checks, health, sources, and andon_on_red")
    main_checks, andon = section.get("main_checks", True), section.get("andon_on_red", False)
    if not isinstance(main_checks, bool) or not isinstance(andon, bool):
        raise IncidentError(".agents/dispatch.json incidents main_checks and andon_on_red must be true or false")
    listed = section.get("health", [])
    health: list[Health] = []
    for item in listed if isinstance(listed, list) else [None]:
        if not isinstance(item, dict) or set(item) != {"name", "url"} or not HEALTH_NAME.match(str(item["name"])) or not health_url(str(item["url"])):
            raise IncidentError(".agents/dispatch.json incidents.health must list {name, url} with a short name and a plain https URL on a named host")
        health.append(Health(str(item["name"]), str(item["url"])))
    if len({h.name for h in health}) != len(health):
        raise IncidentError(".agents/dispatch.json incidents.health names must be unique")
    try:
        sources = validate_sources(section.get("sources", []), alert_hosts)
    except IncidentSourceError as err:
        raise IncidentError(f".agents/dispatch.json incidents.sources {err}") from None
    return Settings(main_checks, tuple(health), sources, andon)


def health_url(url: str) -> bool:
    if not url.startswith("https://"):
        return False
    try:
        parts = urlsplit(url)
        return bool(HOST_NAME.match((parts.hostname or "").lower())) and parts.port is None and "@" not in parts.netloc and not parts.fragment
    except ValueError:
        return False


def main_observation(branch: str, check_runs: list[dict], not_started: Callable[[dict], bool]) -> Observation:
    red = [r for r in check_runs if r.get("status") == "completed" and str(r.get("conclusion") or "").lower() in FAILED_CONCLUSIONS]
    failed = sorted({str(r.get("name") or "check") for r in red if not not_started(r)})
    if failed:
        return Observation(MAIN_KEY, f"{branch} is red: {', '.join(failed)}", True, 1)
    unfinished = not check_runs or any(r.get("status") != "completed" for r in check_runs)
    return Observation(MAIN_KEY, f"{branch} is green", None if red or unfinished else False, 1)


def health_observation(check: Health, status: int | None) -> Observation:
    failing = status is None or not 200 <= status < 300
    what = "does not answer" if status is None else f"returns HTTP {status}"
    return Observation(f"health:{check.name}", f"health check {check.name} {what}", failing, HEALTH_STREAK)


class IncidentStore:
    def __init__(self, home: Path, bot: str) -> None:
        self.path = home / "incidents" / f"{bot}.json"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_name(f"{self.path.name}.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        for key in ("open", "streaks", "problems"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def write(self, data: dict) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(data))
        tmp.replace(self.path)


def advance(data: dict, observed: list[Observation], unreadable: dict[str, str], head: str, now: float) -> Report:
    resolved: list[dict[str, object]] = []
    fresh: dict[str, Observation] = {}
    for obs in observed:
        if obs.failing is None:
            continue
        streak = data["streaks"].get(obs.key, 0)
        streak = max(streak, 0) + 1 if obs.failing else min(streak, 0) - 1
        data["streaks"][obs.key] = streak
        incident = data["open"].get(obs.key)
        if obs.failing and incident is None and streak >= obs.needed:
            data["open"][obs.key] = {"title": obs.title, "since": now, "head": head, "pending": True}
        elif obs.failing and incident is not None:
            incident["title"] = obs.title
            if obs.key == MAIN_KEY and head:
                incident["head"] = head
        elif not obs.failing and incident is not None and -streak >= obs.needed:
            resolved.append(closed(data, obs.key, now, "passing again"))
        fresh[obs.key] = obs
    watched = {obs.key for obs in observed} | set(unreadable)
    for key in [k for k in data["open"] if k not in watched]:
        resolved.append(closed(data, key, now, "no longer watched"))
    for key in [k for k in data["streaks"] if k not in watched]:
        del data["streaks"][key]
    new = [{"key": key, "title": incident["title"], "head": incident.get("head") or "", "since": incident["since"],
            "examples": list(fresh[key].examples) if key in fresh else []}
           for key, incident in sorted(data["open"].items()) if incident.get("pending")]
    return Report(new, resolved)


def closed(data: dict, key: str, now: float, why: str) -> dict[str, object]:
    incident = data["open"].pop(key)
    return {"key": key, "title": incident["title"], "why": why, "minutes": max(1, int((now - float(incident["since"])) // 60))}


def acknowledge(data: dict, key: str) -> None:
    incident = data["open"].get(key) if INCIDENT_KEY.match(key) else None
    if incident is None:
        raise IncidentError(f"no open incident {key}")
    incident["pending"] = False


def fetch_status(url: str) -> int | None:
    for _ in range(MAX_REDIRECTS + 1):
        try:
            status, headers, _ = https_get(url, {"User-Agent": "faker-kit-incidents"}, limit=0, public_only=True)
        except IncidentSourceError:
            return None
        target = urljoin(url, headers.get("location", ""))
        if not 300 <= status < 400 or not target.startswith("https://"):
            return status
        url = target
    return None


def alert_secrets(sources: list[dict[str, object]], env: Mapping[str, str]) -> list[str]:
    names = [str(source[key]) for source in sources for key in ("token_env", "app_key_env") if key in source]
    return [env[name] for name in names if env.get(name)]


def redact(text: str, secrets: Collection[str], shapes: re.Pattern[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return shapes.sub(REDACTED, text)


def fence(text: str) -> str:
    return " ".join(text.replace("`", "'").replace("<", "(").replace(">", ")").split())[:200]


class Watch:
    def __init__(self, gh: Gh, store: IncidentStore, alert_hosts: frozenset[str], env: Mapping[str, str], shapes: re.Pattern[str],
                 fetch_api: FetchJson, fetch_url: FetchStatus, now: Callable[[], float]) -> None:
        self.gh, self.store, self.alert_hosts, self.env, self.shapes = gh, store, alert_hosts, env, shapes
        self.fetch_api, self.fetch_url, self.now = fetch_api, fetch_url, now

    def not_started(self, run: dict) -> bool:
        if str(run.get("conclusion") or "").lower() not in FAILED_CONCLUSIONS or not (run.get("output") or {}).get("annotations_count"):
            return False
        notes = self.gh.pages(f"repos/{self.gh.repo}/check-runs/{int(run['id'])}/annotations")
        return any(NOT_STARTED in str(note.get("message") or "") for note in notes)

    def acknowledge(self, key: str) -> dict:
        with self.store.locked():
            data = self.store.read()
            try:
                acknowledge(data, key)
            except IncidentError as err:
                raise Refused(str(err)) from None
            self.store.write(data)
        return {"acknowledged": key}

    def run(self) -> dict:
        default = self.gh.default_branch()
        try:
            conf = settings(self.gh.file_text(".agents/dispatch.json", default), self.alert_hosts)
        except IncidentError as err:
            raise Refused(str(err)) from None
        head = ""
        observed: list[Observation] = []
        if conf.main_checks:
            head = str(self.gh.json(f"repos/{self.gh.repo}/commits/{quote(default, safe='/')}").get("sha") or "")
            if not SHA.match(head):
                raise Refused(f"cannot read the head of {default}")
            observed.append(main_observation(default, self.gh.check_runs(head), self.not_started))
        observed += [health_observation(check, self.fetch_url(check.url)) for check in conf.health]
        unreadable: dict[str, str] = {}
        for result in poll_sources(conf.sources, self.fetch_api, self.env, self.alert_hosts):
            if isinstance(result, ExternalObservation):
                observed.append(Observation(result.key, result.title, result.failing, HEALTH_STREAK, tuple(fence(e) for e in result.examples)))
            else:
                unreadable[result.key] = result.problem
        secrets = alert_secrets(conf.sources, self.env)
        with self.store.locked():
            data = self.store.read()
            report = advance(data, observed, unreadable, head, self.now())
            said = {key: problem for key, problem in unreadable.items() if data["problems"].get(key) != problem}
            data["problems"] = unreadable
            self.store.write(data)
        stop = conf.andon_on_red and MAIN_KEY in data["open"]
        out = {"branch": default, "new": report.new, "resolved": report.resolved, "open": len(data["open"]),
               "unreadable": [{"source": key, "problem": problem} for key, problem in sorted(said.items())], "stop_the_line": stop}
        return json.loads(redact(json.dumps(out), secrets, self.shapes))
