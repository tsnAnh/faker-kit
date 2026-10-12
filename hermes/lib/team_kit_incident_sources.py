from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from email.message import Message
from typing import IO, Any  # noqa: TID251 - alert APIs answer untyped JSON; each source parses it into ExternalObservation

FetchJson = Callable[[str, Mapping[str, str]], Any]

ENV_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
SOURCE_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
SLUG = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")
DATADOG_SITE = re.compile(r"\A[A-Za-z0-9.-]{1,128}\Z")
SENTRY_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "fatal": 50}
DEFAULT_GRAFANA_ALERTS_PATH = "/api/prometheus/grafana/api/v1/alerts"
HTTP_TIMEOUT_SECONDS = 10


ALERT_ENV_PREFIX = "TEAM_KIT_ALERT_"
FORBIDDEN_ENV = re.compile(r"\A(GH_TOKEN|GH_[A-Z0-9_]*|GITHUB_[A-Z0-9_]*)\Z", re.IGNORECASE)
HOST_NAME = re.compile(r"\A[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z")
DEFAULT_SENTRY_HOST = "sentry.io"
DEFAULT_PAGERDUTY_HOST = "api.pagerduty.com"
DATADOG_SITES = ("datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "us5.datadoghq.com",
                 "ap1.datadoghq.com", "ap2.datadoghq.com", "ddog-gov.com")
DATADOG_HOSTS = frozenset(f"api.{site}" for site in DATADOG_SITES)
DEFAULT_SENTRY_LEVEL = "error"
MAX_EXAMPLES = 3


class IncidentSourceError(Exception):
    pass


@dataclass(frozen=True)
class ExternalObservation:
    """What one source says. `title` is built from the source key and a count only, so it is safe to put in
    a prompt. `examples` is third-party alert text: data for a fenced block, never for a prompt."""

    key: str
    title: str
    failing: bool
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFailure:
    key: str
    problem: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the token's header to another host, so a redirect is an error."""

    def redirect_request(self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str) -> None:  # noqa: ARG002
        return None


def fetch_json(url: str, headers: Mapping[str, str]) -> Any:  # noqa: ANN401
    request = urllib.request.Request(  # noqa: S310
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "team-kit-incidents", **dict(headers)},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = response.read(2_000_000)
    except urllib.error.HTTPError as err:
        raise IncidentSourceError(f"{_url_for_error(url)} returned HTTP {err.code}") from None
    except Exception:  # noqa: BLE001
        raise IncidentSourceError(f"{_url_for_error(url)} did not answer") from None
    try:
        return json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IncidentSourceError(f"{_url_for_error(url)} returned invalid JSON") from None


def validate_sources(value: Any, alert_hosts: Collection[str] = ()) -> list[dict[str, Any]]:  # noqa: ANN401
    """alert_hosts is the machine's guard.yaml list; only the owner edits it."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise IncidentSourceError("must be a list")
    normalized: list[dict[str, Any]] = []
    keys: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise IncidentSourceError(f"entry {index + 1} must be an object")
        source = dict(item)
        kind = source.get("type")
        name = source.get("name")
        if kind not in {"sentry", "pagerduty", "datadog", "grafana"}:
            raise IncidentSourceError(f"entry {index + 1} type must be sentry, pagerduty, datadog, or grafana")
        if not isinstance(name, str) or not SOURCE_NAME.match(name):
            raise IncidentSourceError(f"{kind} source name must be a short lowercase name")
        key = f"{kind}:{name}"
        if key in keys:
            raise IncidentSourceError(f"{key} is duplicated")
        keys.add(key)
        validator = {
            "sentry": _validate_sentry,
            "pagerduty": _validate_pagerduty,
            "datadog": _validate_datadog,
            "grafana": _validate_grafana,
        }[kind]
        validator(source, frozenset(str(h).lower() for h in alert_hosts))
        normalized.append(source)
    return normalized


def poll_sources(value: Any, fetch: FetchJson = fetch_json, env: Mapping[str, str] | None = None,  # noqa: ANN401
                 alert_hosts: Collection[str] = ()) -> list[ExternalObservation | SourceFailure]:
    """One result per source. A source that fails is a SourceFailure; it never stops the others."""
    results: list[ExternalObservation | SourceFailure] = []
    for source in validate_sources(value, alert_hosts):
        key = f"{source['type']}:{source['name']}"
        try:
            results.append(_poll(source, fetch, os.environ if env is None else env))
        except IncidentSourceError as err:
            results.append(SourceFailure(key, str(err)[:200]))
        except Exception as err:  # noqa: BLE001 - an odd payload must not stop the other checks
            results.append(SourceFailure(key, f"{source['type']} source {source['name']} failed: unexpected {type(err).__name__}"))
    return results


def _poll(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    kind = str(source["type"])
    return {
        "sentry": _poll_sentry,
        "pagerduty": _poll_pagerduty,
        "datadog": _poll_datadog,
        "grafana": _poll_grafana,
    }[kind](source, fetch, env)


def _validate_sentry(source: dict[str, Any], alert_hosts: frozenset[str]) -> None:
    _only(source, {"type", "name", "token_env", "url", "organization", "project", "projects", "environment", "environments", "min_level", "query", "limit"})
    _env(source, "token_env")
    _slug(source, "organization", required=True)
    _slugs(source, "project", "projects")
    _slugs(source, "environment", "environments")
    level = source.get("min_level", DEFAULT_SENTRY_LEVEL)
    if level not in SENTRY_LEVELS:
        raise IncidentSourceError("sentry min_level must be debug, info, warning, error, or fatal")
    _host(source, "url", {DEFAULT_SENTRY_HOST}, alert_hosts, required=False)
    _limit(source)


def _validate_pagerduty(source: dict[str, Any], alert_hosts: frozenset[str]) -> None:
    _only(source, {"type", "name", "token_env", "url", "service_id", "limit"})
    _env(source, "token_env")
    _slug(source, "service_id", required=True)
    _host(source, "url", {DEFAULT_PAGERDUTY_HOST}, alert_hosts, required=False)
    _limit(source)


def _validate_datadog(source: dict[str, Any], alert_hosts: frozenset[str]) -> None:
    _only(source, {"type", "name", "token_env", "app_key_env", "url", "site", "monitor_tags", "limit"})
    _env(source, "token_env")
    _env(source, "app_key_env")
    if "url" in source and "site" in source:
        raise IncidentSourceError("datadog must use either url or site, not both")
    if "url" in source:
        _host(source, "url", DATADOG_HOSTS, alert_hosts, required=True)
    site = source.get("site", "datadoghq.com")
    if not isinstance(site, str) or not DATADOG_SITE.match(site) or "/" in site:
        raise IncidentSourceError("datadog site must be a Datadog site host, such as datadoghq.com or datadoghq.eu")
    if f"api.{site.lower()}" not in DATADOG_HOSTS | alert_hosts:
        raise IncidentSourceError(f"datadog site {site} is not a Datadog site; the owner lists a custom host under alert_hosts in guard.yaml")
    tags = source.get("monitor_tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
        raise IncidentSourceError("datadog monitor_tags must be a list of non-empty strings")
    _limit(source)


def _validate_grafana(source: dict[str, Any], alert_hosts: frozenset[str]) -> None:
    _only(source, {"type", "name", "token_env", "url", "path", "org_id"})
    _env(source, "token_env")
    _host(source, "url", set(), alert_hosts, required=True)  # Grafana is self-hosted: its host is always the owner's to list
    path = source.get("path", DEFAULT_GRAFANA_ALERTS_PATH)
    if (not isinstance(path, str) or not path.startswith("/") or "://" in path or path.startswith("//")
            or any(ch in path for ch in "?#\\ ") or ".." in path):
        raise IncidentSourceError("grafana path must be an absolute API path")
    org_id = source.get("org_id")
    if org_id is not None and (not isinstance(org_id, int) or org_id <= 0):
        raise IncidentSourceError("grafana org_id must be a positive integer")


def _poll_sentry(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = _secret(env, source, "token_env")
    base = str(source.get("url") or "https://sentry.io").rstrip("/")
    query = "is:unresolved"
    if source.get("query"):
        query += f" {source['query']}"
    params: list[tuple[str, Any]] = [("query", query), ("sort", "date"), ("limit", source.get("limit", 100))]
    params.extend(("project", project) for project in _values(source, "project", "projects"))
    params.extend(("environment", environment) for environment in _values(source, "environment", "environments"))
    url = _join(base, f"/api/0/organizations/{urllib.parse.quote(str(source['organization']), safe='')}/issues/", params)
    payload = fetch(url, {"Authorization": f"Bearer {token}"})
    if not isinstance(payload, list):
        raise IncidentSourceError(f"sentry source {source['name']} returned an unexpected response")
    min_level = SENTRY_LEVELS[str(source.get("min_level", DEFAULT_SENTRY_LEVEL))]
    issues = [issue for issue in payload if isinstance(issue, dict) and SENTRY_LEVELS.get(str(issue.get("level") or "").lower(), -1) >= min_level]
    level = source.get("min_level", DEFAULT_SENTRY_LEVEL)
    return _observation("sentry", source, f"{_count(len(issues), 'unresolved issue')} at {level} or above", issues, _sentry_title, token)


def _poll_pagerduty(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = _secret(env, source, "token_env")
    base = str(source.get("url") or "https://api.pagerduty.com").rstrip("/")
    params = [
        ("service_ids[]", source["service_id"]),
        ("statuses[]", "triggered"),
        ("statuses[]", "acknowledged"),
        ("limit", source.get("limit", 100)),
    ]
    payload = fetch(_join(base, "/incidents", params), {
        "Authorization": f"Token token={token}",
        "Accept": "application/vnd.pagerduty+json;version=2",
        "Content-Type": "application/json",
    })
    incidents = payload.get("incidents") if isinstance(payload, dict) else None
    if not isinstance(incidents, list):
        raise IncidentSourceError(f"pagerduty source {source['name']} returned an unexpected response")
    open_incidents = [
        incident for incident in incidents
        if isinstance(incident, dict) and str(incident.get("status") or "").lower() in {"triggered", "acknowledged"}
    ]
    return _observation("pagerduty", source, f"{_count(len(open_incidents), 'open incident')}", open_incidents, _pagerduty_title, token)


def _poll_datadog(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    api_key = _secret(env, source, "token_env")
    app_key = _secret(env, source, "app_key_env")
    base = str(source.get("url") or f"https://api.{source.get('site', 'datadoghq.com')}").rstrip("/")
    params: list[tuple[str, Any]] = [("group_states", "alert")]
    params.extend(("monitor_tags", tag) for tag in source.get("monitor_tags", []))
    if source.get("limit"):
        params.append(("page_size", source["limit"]))
    payload = fetch(_join(base, "/api/v1/monitor", params), {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key})
    monitors = payload if isinstance(payload, list) else payload.get("monitors") if isinstance(payload, dict) else None
    if not isinstance(monitors, list):
        raise IncidentSourceError(f"datadog source {source['name']} returned an unexpected response")
    alerting = [monitor for monitor in monitors if isinstance(monitor, dict) and _datadog_alerting(monitor)]
    return _observation("datadog", source, f"{_count(len(alerting), 'monitor')} in Alert", alerting, _datadog_title, api_key, app_key)


def _poll_grafana(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = _secret(env, source, "token_env")
    base = str(source["url"]).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    if source.get("org_id"):
        headers["X-Grafana-Org-Id"] = str(source["org_id"])
    payload = fetch(f"{base}{source.get('path', DEFAULT_GRAFANA_ALERTS_PATH)}", headers)
    alerts = _grafana_alerts(payload)
    firing = [alert for alert in alerts if isinstance(alert, dict) and _grafana_firing(alert)]
    return _observation("grafana", source, f"{_count(len(firing), 'firing alert')}", firing, _grafana_title, token)


def _only(source: Mapping[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(source) - allowed)
    if extra:
        raise IncidentSourceError(f"{source.get('type', 'source')} source has unsupported setting {extra[0]}")


def _env(source: Mapping[str, Any], key: str) -> None:
    value = source.get(key)
    if not isinstance(value, str) or not ENV_NAME.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must name an environment variable")
    if FORBIDDEN_ENV.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must not name the bot's GitHub credentials")
    if not value.startswith(ALERT_ENV_PREFIX) or len(value) == len(ALERT_ENV_PREFIX):
        raise IncidentSourceError(f"{source['type']} {key} must start with {ALERT_ENV_PREFIX}")


def _secret(env: Mapping[str, str], source: Mapping[str, Any], key: str) -> str:
    name = str(source[key])
    value = env.get(name)
    if not value:
        raise IncidentSourceError(f"{source['type']} source {source['name']} token env {name} is unset")
    return value


def _slug(source: Mapping[str, Any], key: str, required: bool) -> None:
    value = source.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not SLUG.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must be a short id or slug")


def _slugs(source: Mapping[str, Any], singular: str, plural: str) -> None:
    if singular in source and plural in source:
        raise IncidentSourceError(f"{source['type']} must use either {singular} or {plural}, not both")
    if singular in source:
        _slug(source, singular, required=True)
    if plural in source:
        values = source.get(plural)
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or not SLUG.match(value) for value in values):
            raise IncidentSourceError(f"{source['type']} {plural} must be a list of short ids or slugs")


def _host(source: Mapping[str, Any], key: str, pinned: Collection[str], alert_hosts: frozenset[str], required: bool) -> None:
    """An https URL on a pinned host or one the owner listed in guard.yaml alert_hosts: the token goes only there."""
    value = source.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.startswith("https://"):
        raise IncidentSourceError(f"{source['type']} {key} must be an https URL")
    try:
        parts = urllib.parse.urlsplit(value)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise IncidentSourceError(f"{source['type']} {key} must be an https URL") from None
    if "@" in parts.netloc or port is not None or parts.query or parts.fragment or not HOST_NAME.match(host):
        raise IncidentSourceError(f"{source['type']} {key} must be a plain https URL: a host, no login, port, query, or fragment")
    if host not in {h.lower() for h in pinned} | alert_hosts:
        raise IncidentSourceError(
            f"{source['type']} source host {host} is not allowed; the owner lists it under alert_hosts in guard.yaml on this machine")


def _limit(source: Mapping[str, Any]) -> None:
    value = source.get("limit", 100)
    if not isinstance(value, int) or value < 1 or value > 100:
        raise IncidentSourceError(f"{source['type']} limit must be between 1 and 100")


def _values(source: Mapping[str, Any], singular: str, plural: str) -> list[str]:
    if singular in source:
        return [str(source[singular])]
    if plural in source:
        return [str(value) for value in source[plural]]
    return []


def _join(base: str, path: str, params: list[tuple[str, Any]]) -> str:
    query = urllib.parse.urlencode(params, doseq=True)
    return f"{base}{path}" + (f"?{query}" if query else "")


def _url_for_error(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _observation(kind: str, source: Mapping[str, Any], what: str, items: list[dict[str, Any]],
                 pick: Callable[[dict[str, Any]], str | None], *secrets: str) -> ExternalObservation:
    """The title carries the source key and a count, nothing a third party wrote."""
    examples: list[str] = []
    for item in items:
        value = pick(item)
        if value:
            text = _one_line(value)
            for secret in secrets:
                text = _redact(text, secret)
            examples.append(text)
        if len(examples) == MAX_EXAMPLES:
            break
    key = f"{kind}:{source['name']}"
    return ExternalObservation(key, f"{key}: {what}", bool(items), tuple(examples))


def _one_line(value: Any) -> str:  # noqa: ANN401
    return " ".join(str(value).split())[:120]


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _sentry_title(issue: dict[str, Any]) -> str | None:
    return str(issue.get("shortId") or issue.get("title") or (issue.get("metadata") or {}).get("title") or "") or None


def _pagerduty_title(incident: dict[str, Any]) -> str | None:
    return str(incident.get("title") or incident.get("summary") or incident.get("id") or "") or None


def _datadog_alerting(monitor: dict[str, Any]) -> bool:
    if str(monitor.get("overall_state") or monitor.get("status") or "").lower() == "alert":
        return True
    groups = (monitor.get("state") or {}).get("groups") if isinstance(monitor.get("state"), dict) else {}
    return any(isinstance(group, dict) and str(group.get("status") or "").lower() == "alert" for group in (groups or {}).values())


def _datadog_title(monitor: dict[str, Any]) -> str | None:
    return str(monitor.get("name") or monitor.get("id") or "") or None


def _grafana_alerts(payload: Any) -> list[Any]:  # noqa: ANN401
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("alerts"), list):
            return list(data["alerts"])
        if isinstance(payload.get("alerts"), list):
            return list(payload["alerts"])
    if isinstance(payload, list):
        return payload
    raise IncidentSourceError("grafana source returned an unexpected response")


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _grafana_firing(alert: dict[str, Any]) -> bool:
    status = _mapping(alert.get("status"))
    return str(alert.get("state") or status.get("state") or "").lower() in {"firing", "active"}


def _grafana_title(alert: dict[str, Any]) -> str | None:
    labels = _mapping(alert.get("labels"))
    annotations = _mapping(alert.get("annotations"))
    return str(labels.get("alertname") or annotations.get("summary") or annotations.get("title") or alert.get("fingerprint") or "") or None
