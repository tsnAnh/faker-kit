from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable


FetchJson = Callable[[str, Mapping[str, str]], Any]

ENV_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
SOURCE_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
SLUG = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")
DATADOG_SITE = re.compile(r"\A[A-Za-z0-9.-]{1,128}\Z")
SENTRY_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "fatal": 50}
DEFAULT_GRAFANA_ALERTS_PATH = "/api/prometheus/grafana/api/v1/alerts"
HTTP_TIMEOUT_SECONDS = 10


class IncidentSourceError(Exception):
    pass


@dataclass(frozen=True)
class ExternalObservation:
    key: str
    title: str
    failing: bool


def fetch_json(url: str, headers: Mapping[str, str]) -> Any:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "team-kit-incidents", **dict(headers)},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310 - URLs are validated as https.
            body = response.read(2_000_000)
    except urllib.error.HTTPError as err:
        raise IncidentSourceError(f"{_url_for_error(url)} returned HTTP {err.code}") from None
    except Exception:
        raise IncidentSourceError(f"{_url_for_error(url)} did not answer") from None
    try:
        return json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IncidentSourceError(f"{_url_for_error(url)} returned invalid JSON") from None


def validate_sources(value: Any) -> list[dict[str, Any]]:
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
        validator(source)
        normalized.append(source)
    return normalized


def poll_sources(value: Any, fetch: FetchJson = fetch_json, env: Mapping[str, str] | None = None) -> list[ExternalObservation]:
    return [_poll(source, fetch, env or os.environ) for source in validate_sources(value)]


def _poll(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    kind = str(source["type"])
    return {
        "sentry": _poll_sentry,
        "pagerduty": _poll_pagerduty,
        "datadog": _poll_datadog,
        "grafana": _poll_grafana,
    }[kind](source, fetch, env)


def _validate_sentry(source: dict[str, Any]) -> None:
    _only(source, {"type", "name", "token_env", "url", "organization", "project", "projects", "environment", "environments", "min_level", "query", "limit"})
    _env(source, "token_env")
    _slug(source, "organization", required=True)
    _slugs(source, "project", "projects")
    _slugs(source, "environment", "environments")
    level = source.get("min_level")
    if level not in SENTRY_LEVELS:
        raise IncidentSourceError("sentry min_level must be debug, info, warning, error, or fatal")
    _https(source, "url", required=False)
    _limit(source)


def _validate_pagerduty(source: dict[str, Any]) -> None:
    _only(source, {"type", "name", "token_env", "url", "service_id", "limit"})
    _env(source, "token_env")
    _slug(source, "service_id", required=True)
    _https(source, "url", required=False)
    _limit(source)


def _validate_datadog(source: dict[str, Any]) -> None:
    _only(source, {"type", "name", "token_env", "app_key_env", "url", "site", "monitor_tags", "limit"})
    _env(source, "token_env")
    _env(source, "app_key_env")
    if "url" in source:
        _https(source, "url", required=True)
    site = source.get("site", "datadoghq.com")
    if not isinstance(site, str) or not DATADOG_SITE.match(site) or "/" in site:
        raise IncidentSourceError("datadog site must be a Datadog site host, such as datadoghq.com or datadoghq.eu")
    tags = source.get("monitor_tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
        raise IncidentSourceError("datadog monitor_tags must be a list of non-empty strings")
    _limit(source)


def _validate_grafana(source: dict[str, Any]) -> None:
    _only(source, {"type", "name", "token_env", "url", "path", "org_id"})
    _env(source, "token_env")
    _https(source, "url", required=True)
    path = source.get("path", DEFAULT_GRAFANA_ALERTS_PATH)
    if not isinstance(path, str) or not path.startswith("/") or "://" in path:
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
    min_level = SENTRY_LEVELS[str(source["min_level"])]
    issues = [issue for issue in payload if isinstance(issue, dict) and SENTRY_LEVELS.get(str(issue.get("level") or "").lower(), -1) >= min_level]
    title = f"Sentry {source['name']}: {len(issues)} unresolved issue(s) at {source['min_level']} or above{_examples(issues, _sentry_title)}"
    return ExternalObservation(f"sentry:{source['name']}", _redact(title, token), bool(issues))


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
    title = f"PagerDuty {source['name']}: {len(open_incidents)} open incident(s) on service {source['service_id']}{_examples(open_incidents, _pagerduty_title)}"
    return ExternalObservation(f"pagerduty:{source['name']}", _redact(title, token), bool(open_incidents))


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
    title = f"Datadog {source['name']}: {len(alerting)} monitor(s) in Alert{_examples(alerting, _datadog_title)}"
    return ExternalObservation(f"datadog:{source['name']}", _redact(_redact(title, api_key), app_key), bool(alerting))


def _poll_grafana(source: dict[str, Any], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = _secret(env, source, "token_env")
    base = str(source["url"]).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    if source.get("org_id"):
        headers["X-Grafana-Org-Id"] = str(source["org_id"])
    payload = fetch(f"{base}{source.get('path', DEFAULT_GRAFANA_ALERTS_PATH)}", headers)
    alerts = _grafana_alerts(payload)
    firing = [alert for alert in alerts if isinstance(alert, dict) and _grafana_firing(alert)]
    title = f"Grafana {source['name']}: {len(firing)} firing alert(s){_examples(firing, _grafana_title)}"
    return ExternalObservation(f"grafana:{source['name']}", _redact(title, token), bool(firing))


def _only(source: Mapping[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(source) - allowed)
    if extra:
        raise IncidentSourceError(f"{source.get('type', 'source')} source has unsupported setting {extra[0]}")


def _env(source: Mapping[str, Any], key: str) -> None:
    value = source.get(key)
    if not isinstance(value, str) or not ENV_NAME.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must name an environment variable")


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


def _https(source: Mapping[str, Any], key: str, required: bool) -> None:
    value = source.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.startswith("https://"):
        raise IncidentSourceError(f"{source['type']} {key} must be an https URL")


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


def _examples(items: list[dict[str, Any]], pick: Callable[[dict[str, Any]], str | None]) -> str:
    values = []
    for item in items:
        value = pick(item)
        if value:
            values.append(_one_line(value))
        if len(values) == 3:
            break
    return ": " + ", ".join(values) if values else ""


def _one_line(value: Any) -> str:
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


def _grafana_alerts(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("alerts"), list):
            return payload["data"]["alerts"]
        if isinstance(payload.get("alerts"), list):
            return payload["alerts"]
    if isinstance(payload, list):
        return payload
    raise IncidentSourceError("grafana source returned an unexpected response")


def _grafana_firing(alert: dict[str, Any]) -> bool:
    status = alert.get("status") if isinstance(alert.get("status"), dict) else {}
    return str(alert.get("state") or status.get("state") or "").lower() in {"firing", "active"}


def _grafana_title(alert: dict[str, Any]) -> str | None:
    labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
    annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
    return str(labels.get("alertname") or annotations.get("summary") or annotations.get("title") or alert.get("fingerprint") or "") or None
