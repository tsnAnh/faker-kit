from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import urllib.parse
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass

FetchJson = Callable[[str, Mapping[str, str]], object]

ENV_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
SOURCE_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
SLUG = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")
DATADOG_SITE = re.compile(r"\A[A-Za-z0-9.-]{1,128}\Z")
HOST_NAME = re.compile(r"\A[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+\Z")
SENTRY_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "fatal": 50}
SOURCE_KINDS = ("sentry", "pagerduty", "datadog", "grafana")
DEFAULT_GRAFANA_ALERTS_PATH = "/api/prometheus/grafana/api/v1/alerts"
HTTP_TIMEOUT_SECONDS = 10
MAX_BODY_BYTES = 2_000_000
ALERT_ENV_PREFIX = "FAKER_KIT_ALERT_"
FORBIDDEN_ENV = re.compile(r"\A(GH_[A-Z0-9_]*|GITHUB_[A-Z0-9_]*)\Z", re.IGNORECASE)
DEFAULT_SENTRY_HOST = "sentry.io"
DEFAULT_PAGERDUTY_HOST = "api.pagerduty.com"
DATADOG_SITES = ("datadoghq.com", "datadoghq.eu", "us3.datadoghq.com", "us5.datadoghq.com", "ap1.datadoghq.com", "ap2.datadoghq.com", "ddog-gov.com")
DATADOG_HOSTS = frozenset(f"api.{site}" for site in DATADOG_SITES)
DEFAULT_SENTRY_LEVEL = "error"
MAX_EXAMPLES = 3
EXAMPLE_CHARS = 120
ALLOWED = {
    "sentry": {"type", "name", "token_env", "url", "organization", "project", "projects", "environment", "environments", "min_level", "query", "limit"},
    "pagerduty": {"type", "name", "token_env", "url", "service_id", "limit"},
    "datadog": {"type", "name", "token_env", "app_key_env", "url", "site", "monitor_tags", "limit"},
    "grafana": {"type", "name", "token_env", "url", "path", "org_id"},
}


class IncidentSourceError(Exception):
    pass


@dataclass(frozen=True)
class ExternalObservation:
    key: str
    title: str
    failing: bool
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFailure:
    key: str
    problem: str


class PinnedConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int | None, address: str) -> None:
        super().__init__(host, port, timeout=HTTP_TIMEOUT_SECONDS)
        self.address = address

    def connect(self) -> None:
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def public_address(host: str, port: int | None) -> str:
    try:
        found = {str(item[4][0]) for item in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise IncidentSourceError(f"{host} did not resolve") from None
    parsed = [ipaddress.ip_address(address.split("%")[0]) for address in found]
    parsed = [a.ipv4_mapped if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped else a for a in parsed]
    if not found or not all(a.is_global for a in parsed):
        raise IncidentSourceError(f"{host} resolves to a loopback, link-local, or private address")
    return sorted(found)[0]


def https_get(url: str, headers: Mapping[str, str], limit: int = MAX_BODY_BYTES, public_only: bool = False) -> tuple[int, dict[str, str], bytes]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise IncidentSourceError(f"{url_for_error(url)} is not an https URL")
    if public_only:
        connection: http.client.HTTPSConnection = PinnedConnection(parts.hostname, parts.port, public_address(parts.hostname, parts.port))
    else:
        connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        connection.request("GET", (parts.path or "/") + (f"?{parts.query}" if parts.query else ""), headers=dict(headers))
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read(limit)
    except (OSError, http.client.HTTPException):
        raise IncidentSourceError(f"{url_for_error(url)} did not answer") from None
    finally:
        connection.close()


def fetch_json(url: str, headers: Mapping[str, str]) -> object:
    status, _, body = https_get(url, {"Accept": "application/json", "User-Agent": "faker-kit-incidents", **dict(headers)})
    if status != 200:
        raise IncidentSourceError(f"{url_for_error(url)} returned HTTP {status}")
    try:
        return json.loads(body.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IncidentSourceError(f"{url_for_error(url)} returned invalid JSON") from None


def validate_sources(value: object, alert_hosts: Collection[str] = ()) -> list[dict[str, object]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise IncidentSourceError("must be a list")
    hosts = frozenset(str(h).lower() for h in alert_hosts)
    normalized: list[dict[str, object]] = []
    keys: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise IncidentSourceError(f"entry {index + 1} must be an object")
        source = {str(k): v for k, v in item.items()}
        kind, name = source.get("type"), source.get("name")
        if kind not in SOURCE_KINDS:
            raise IncidentSourceError(f"entry {index + 1} type must be sentry, pagerduty, datadog, or grafana")
        if not isinstance(name, str) or not SOURCE_NAME.match(name):
            raise IncidentSourceError(f"{kind} source name must be a short lowercase name")
        key = f"{kind}:{name}"
        if key in keys:
            raise IncidentSourceError(f"{key} is duplicated")
        keys.add(key)
        only(source, ALLOWED[str(kind)])
        env_name(source, "token_env")
        validate_kind(str(kind), source, hosts)
        normalized.append(source)
    return normalized


def validate_kind(kind: str, source: dict[str, object], alert_hosts: frozenset[str]) -> None:
    if kind == "sentry":
        slug(source, "organization", required=True)
        slugs(source, "project", "projects")
        slugs(source, "environment", "environments")
        if source.get("min_level", DEFAULT_SENTRY_LEVEL) not in SENTRY_LEVELS:
            raise IncidentSourceError("sentry min_level must be debug, info, warning, error, or fatal")
        host(source, "url", {DEFAULT_SENTRY_HOST}, alert_hosts, required=False)
        limit(source)
    elif kind == "pagerduty":
        slug(source, "service_id", required=True)
        host(source, "url", {DEFAULT_PAGERDUTY_HOST}, alert_hosts, required=False)
        limit(source)
    elif kind == "datadog":
        env_name(source, "app_key_env")
        if "url" in source and "site" in source:
            raise IncidentSourceError("datadog must use either url or site, not both")
        if "url" in source:
            host(source, "url", DATADOG_HOSTS, alert_hosts, required=True)
        site = source.get("site", "datadoghq.com")
        if not isinstance(site, str) or not DATADOG_SITE.match(site) or "/" in site:
            raise IncidentSourceError("datadog site must be a Datadog site host, such as datadoghq.com or datadoghq.eu")
        if f"api.{site.lower()}" not in DATADOG_HOSTS | alert_hosts:
            raise IncidentSourceError(f"datadog site {site} is not a Datadog site; the owner lists a custom host under alert_hosts in guard.yaml")
        tags = source.get("monitor_tags", [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags):
            raise IncidentSourceError("datadog monitor_tags must be a list of non-empty strings")
        limit(source)
    else:
        host(source, "url", set(), alert_hosts, required=True)
        path = source.get("path", DEFAULT_GRAFANA_ALERTS_PATH)
        if not isinstance(path, str) or not path.startswith("/") or "://" in path or path.startswith("//") or any(ch in path for ch in "?#\\ ") or ".." in path:
            raise IncidentSourceError("grafana path must be an absolute API path")
        org_id = source.get("org_id")
        if org_id is not None and (not isinstance(org_id, int) or isinstance(org_id, bool) or org_id <= 0):
            raise IncidentSourceError("grafana org_id must be a positive integer")


def poll_sources(value: object, fetch: FetchJson, env: Mapping[str, str], alert_hosts: Collection[str] = ()) -> list[ExternalObservation | SourceFailure]:
    results: list[ExternalObservation | SourceFailure] = []
    for source in validate_sources(value, alert_hosts):
        key = f"{source['type']}:{source['name']}"
        try:
            results.append(poll_one(source, fetch, env))
        except IncidentSourceError as err:
            results.append(SourceFailure(key, str(err)[:200]))
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            results.append(SourceFailure(key, f"{source['type']} source {source['name']} failed: unexpected {type(err).__name__}"))
    return results


def poll_one(source: dict[str, object], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    poll = {"sentry": poll_sentry, "pagerduty": poll_pagerduty, "datadog": poll_datadog, "grafana": poll_grafana}[str(source["type"])]
    return poll(source, fetch, env)


def poll_sentry(source: dict[str, object], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = secret(env, source, "token_env")
    base = str(source.get("url") or "https://sentry.io").rstrip("/")
    query = "is:unresolved" + (f" {source['query']}" if source.get("query") else "")
    params: list[tuple[str, object]] = [("query", query), ("sort", "date"), ("limit", source.get("limit", 100))]
    params += [("project", p) for p in values(source, "project", "projects")]
    params += [("environment", e) for e in values(source, "environment", "environments")]
    url = join(base, f"/api/0/organizations/{urllib.parse.quote(str(source['organization']), safe='')}/issues/", params)
    payload = fetch(url, {"Authorization": f"Bearer {token}"})
    if not isinstance(payload, list):
        raise IncidentSourceError(f"sentry source {source['name']} returned an unexpected response")
    level = str(source.get("min_level", DEFAULT_SENTRY_LEVEL))
    issues = [i for i in payload if isinstance(i, dict) and SENTRY_LEVELS.get(str(i.get("level") or "").lower(), -1) >= SENTRY_LEVELS[level]]
    return observation("sentry", source, f"{count(len(issues), 'unresolved issue')} at {level} or above", issues, sentry_title, token)


def poll_pagerduty(source: dict[str, object], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = secret(env, source, "token_env")
    base = str(source.get("url") or "https://api.pagerduty.com").rstrip("/")
    params: list[tuple[str, object]] = [("service_ids[]", source["service_id"]), ("statuses[]", "triggered"), ("statuses[]", "acknowledged"), ("limit", source.get("limit", 100))]
    payload = fetch(join(base, "/incidents", params), {"Authorization": f"Token token={token}", "Accept": "application/vnd.pagerduty+json;version=2",
                                                        "Content-Type": "application/json"})
    incidents = payload.get("incidents") if isinstance(payload, dict) else None
    if not isinstance(incidents, list):
        raise IncidentSourceError(f"pagerduty source {source['name']} returned an unexpected response")
    open_incidents = [i for i in incidents if isinstance(i, dict) and str(i.get("status") or "").lower() in {"triggered", "acknowledged"}]
    return observation("pagerduty", source, count(len(open_incidents), "open incident"), open_incidents, pagerduty_title, token)


def poll_datadog(source: dict[str, object], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    api_key = secret(env, source, "token_env")
    app_key = secret(env, source, "app_key_env")
    base = str(source.get("url") or f"https://api.{source.get('site', 'datadoghq.com')}").rstrip("/")
    params: list[tuple[str, object]] = [("group_states", "alert")]
    tags = source.get("monitor_tags")
    params += [("monitor_tags", tag) for tag in (tags if isinstance(tags, list) else [])]
    if source.get("limit"):
        params.append(("page_size", source["limit"]))
    payload = fetch(join(base, "/api/v1/monitor", params), {"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key})
    monitors = payload if isinstance(payload, list) else payload.get("monitors") if isinstance(payload, dict) else None
    if not isinstance(monitors, list):
        raise IncidentSourceError(f"datadog source {source['name']} returned an unexpected response")
    alerting = [m for m in monitors if isinstance(m, dict) and datadog_alerting(m)]
    return observation("datadog", source, f"{count(len(alerting), 'monitor')} in Alert", alerting, datadog_title, api_key, app_key)


def poll_grafana(source: dict[str, object], fetch: FetchJson, env: Mapping[str, str]) -> ExternalObservation:
    token = secret(env, source, "token_env")
    headers = {"Authorization": f"Bearer {token}"}
    if source.get("org_id"):
        headers["X-Grafana-Org-Id"] = str(source["org_id"])
    payload = fetch(f"{str(source['url']).rstrip('/')}{source.get('path', DEFAULT_GRAFANA_ALERTS_PATH)}", headers)
    firing = [a for a in grafana_alerts(payload) if isinstance(a, dict) and grafana_firing(a)]
    return observation("grafana", source, count(len(firing), "firing alert"), firing, grafana_title, token)


def only(source: Mapping[str, object], allowed: set[str]) -> None:
    extra = sorted(set(source) - allowed)
    if extra:
        raise IncidentSourceError(f"{source.get('type', 'source')} source has unsupported setting {extra[0]}")


def env_name(source: Mapping[str, object], key: str) -> None:
    value = source.get(key)
    if not isinstance(value, str) or not ENV_NAME.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must name an environment variable")
    if FORBIDDEN_ENV.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must not name the bot's GitHub credentials")
    prefix = f"{ALERT_ENV_PREFIX}{str(source['type']).upper()}"
    if value != prefix and not value.startswith(f"{prefix}_"):
        raise IncidentSourceError(f"{source['type']} {key} must be {prefix} or start with {prefix}_, so the token goes only to {source['type']} hosts")


def secret(env: Mapping[str, str], source: Mapping[str, object], key: str) -> str:
    name = str(source[key])
    value = env.get(name)
    if not value:
        raise IncidentSourceError(f"{source['type']} source {source['name']} token {name} is not in the bot's .env")
    return value


def slug(source: Mapping[str, object], key: str, required: bool) -> None:
    value = source.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not SLUG.match(value):
        raise IncidentSourceError(f"{source['type']} {key} must be a short id or slug")


def slugs(source: Mapping[str, object], singular: str, plural: str) -> None:
    if singular in source and plural in source:
        raise IncidentSourceError(f"{source['type']} must use either {singular} or {plural}, not both")
    if singular in source:
        slug(source, singular, required=True)
    if plural in source:
        listed = source.get(plural)
        if not isinstance(listed, list) or not listed or any(not isinstance(item, str) or not SLUG.match(item) for item in listed):
            raise IncidentSourceError(f"{source['type']} {plural} must be a list of short ids or slugs")


def host(source: Mapping[str, object], key: str, pinned: Collection[str], alert_hosts: frozenset[str], required: bool) -> None:
    value = source.get(key)
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.startswith("https://"):
        raise IncidentSourceError(f"{source['type']} {key} must be an https URL")
    try:
        parts = urllib.parse.urlsplit(value)
        name = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        raise IncidentSourceError(f"{source['type']} {key} must be an https URL") from None
    if "@" in parts.netloc or port is not None or parts.query or parts.fragment or not HOST_NAME.match(name):
        raise IncidentSourceError(f"{source['type']} {key} must be a plain https URL: a host, no login, port, query, or fragment")
    if name not in {h.lower() for h in pinned} | alert_hosts:
        raise IncidentSourceError(f"{source['type']} source host {name} is not allowed; the owner lists it under alert_hosts in guard.yaml on this machine")


def limit(source: Mapping[str, object]) -> None:
    value = source.get("limit", 100)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise IncidentSourceError(f"{source['type']} limit must be between 1 and 100")


def values(source: Mapping[str, object], singular: str, plural: str) -> list[str]:
    if singular in source:
        return [str(source[singular])]
    listed = source.get(plural)
    return [str(v) for v in listed] if isinstance(listed, list) else []


def join(base: str, path: str, params: list[tuple[str, object]]) -> str:
    query = urllib.parse.urlencode(params, doseq=True)
    return f"{base}{path}" + (f"?{query}" if query else "")


def url_for_error(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def observation(kind: str, source: Mapping[str, object], what: str, items: list[dict[str, object]], pick: Callable[[dict[str, object]], str | None],
                *secrets: str) -> ExternalObservation:
    examples: list[str] = []
    for item in items:
        value = pick(item)
        if value:
            text = " ".join(value.split())
            for token in secrets:
                text = text.replace(token, "[redacted]") if token else text
            examples.append(text[:EXAMPLE_CHARS])
        if len(examples) == MAX_EXAMPLES:
            break
    key = f"{kind}:{source['name']}"
    return ExternalObservation(key, f"{key}: {what}", bool(items), tuple(examples))


def text_of(*candidates: object) -> str | None:
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return None


def mapping(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def sentry_title(issue: dict[str, object]) -> str | None:
    return text_of(issue.get("shortId"), issue.get("title"), mapping(issue.get("metadata")).get("title"))


def pagerduty_title(incident: dict[str, object]) -> str | None:
    return text_of(incident.get("title"), incident.get("summary"), incident.get("id"))


def datadog_alerting(monitor: dict[str, object]) -> bool:
    if str(monitor.get("overall_state") or monitor.get("status") or "").lower() == "alert":
        return True
    groups = mapping(mapping(monitor.get("state")).get("groups"))
    return any(str(mapping(group).get("status") or "").lower() == "alert" for group in groups.values())


def datadog_title(monitor: dict[str, object]) -> str | None:
    return text_of(monitor.get("name"), monitor.get("id"))


def grafana_alerts(payload: object) -> list[object]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("alerts"), list):
            return list(data["alerts"])
        if isinstance(payload.get("alerts"), list):
            return list(payload["alerts"])
    if isinstance(payload, list):
        return payload
    raise IncidentSourceError("grafana source returned an unexpected response")


def grafana_firing(alert: dict[str, object]) -> bool:
    status = mapping(alert.get("status"))
    return str(alert.get("state") or status.get("state") or "").lower() in {"firing", "active"}


def grafana_title(alert: dict[str, object]) -> str | None:
    labels, annotations = mapping(alert.get("labels")), mapping(alert.get("annotations"))
    return text_of(labels.get("alertname"), annotations.get("summary"), annotations.get("title"), alert.get("fingerprint"))
