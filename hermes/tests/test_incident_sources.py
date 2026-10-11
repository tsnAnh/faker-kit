"""Response shapes and refusals of the external alert sources. No network: every fetch is a fake."""

import urllib.request

import pytest
import yaml
from conftest import BOTS, guard_text
from team_kit_guard.policy import PolicyError, parse_policy
from team_kit_incident_sources import (
    ExternalObservation,
    IncidentSourceError,
    SourceFailure,
    _NoRedirect,
    poll_sources,
    validate_sources,
)

ENV = {"TEAM_KIT_ALERT_A": "tok-a", "TEAM_KIT_ALERT_B": "tok-b"}
HOSTS = {"grafana.example.com"}


def datadog(**extra):
    return {"type": "datadog", "name": "dd", "token_env": "TEAM_KIT_ALERT_A", "app_key_env": "TEAM_KIT_ALERT_B", **extra}


def grafana(**extra):
    return {"type": "grafana", "name": "gf", "url": "https://grafana.example.com", "token_env": "TEAM_KIT_ALERT_A", **extra}


def poll_one(source, payload, hosts=HOSTS):
    calls = []

    def fetch(url, headers):
        calls.append((url, dict(headers)))
        if isinstance(payload, Exception):
            raise payload
        return payload

    (result,) = poll_sources([source], fetch, ENV, hosts)
    return result, calls


@pytest.mark.parametrize("payload", [
    [{"id": 1, "name": "latency", "overall_state": "Alert"}, {"id": 2, "name": "ok", "overall_state": "OK"}],
    {"monitors": [{"id": 1, "name": "latency", "overall_state": "Alert"}, {"id": 2, "name": "ok", "overall_state": "OK"}]},
    [{"id": 1, "name": "latency", "overall_state": "OK", "state": {"groups": {"host:a": {"status": "Alert"}}}},
     {"id": 2, "name": "ok", "overall_state": "OK"}],
])
def test_datadog_reads_a_list_a_monitors_object_and_group_states(payload):
    result, calls = poll_one(datadog(monitor_tags=["service:api"]), payload)
    assert result == ExternalObservation("datadog:dd", "datadog:dd: 1 monitor in Alert", True, ("latency",))
    url, headers = calls[0]
    assert url.startswith("https://api.datadoghq.com/api/v1/monitor?") and "monitor_tags=service%3Aapi" in url
    assert headers == {"DD-API-KEY": "tok-a", "DD-APPLICATION-KEY": "tok-b"}


def test_datadog_with_nothing_in_alert_passes_and_a_regional_site_uses_its_own_host():
    result, calls = poll_one(datadog(site="datadoghq.eu"), [{"name": "ok", "overall_state": "OK"}])
    assert result.failing is False and result.title == "datadog:dd: 0 monitors in Alert" and result.examples == ()
    assert calls[0][0].startswith("https://api.datadoghq.eu/api/v1/monitor")


@pytest.mark.parametrize("payload", [
    {"status": "success", "data": {"alerts": [{"state": "firing", "labels": {"alertname": "HighErrors"}}, {"state": "inactive", "labels": {"alertname": "Quiet"}}]}},
    {"alerts": [{"state": "Alerting", "labels": {"alertname": "x"}}, {"state": "firing", "labels": {"alertname": "HighErrors"}}, {"state": "normal"}]},
    [{"status": {"state": "active"}, "annotations": {"summary": "HighErrors"}}, {"status": {"state": "suppressed"}}],
])
def test_grafana_reads_the_prometheus_shape_a_flat_alerts_object_and_a_list(payload):
    result, calls = poll_one(grafana(org_id=2), payload)
    assert result.failing is True and result.title == "grafana:gf: 1 firing alert"
    assert result.examples == ("HighErrors",)
    url, headers = calls[0]
    assert url == "https://grafana.example.com/api/prometheus/grafana/api/v1/alerts"
    assert headers == {"Authorization": "Bearer tok-a", "X-Grafana-Org-Id": "2"}


def test_grafana_with_no_firing_alerts_passes():
    result, _ = poll_one(grafana(), {"data": {"alerts": [{"state": "inactive"}]}})
    assert result.failing is False and result.title == "grafana:gf: 0 firing alerts"


def test_sentry_defaults_min_level_to_error():
    source = {"type": "sentry", "name": "s", "organization": "acme", "token_env": "TEAM_KIT_ALERT_A"}
    payload = [{"level": "warning", "title": "w"}, {"level": "error", "title": "e"}, {"level": "fatal", "title": "f"}]
    result, _ = poll_one(source, payload)
    assert result.title == "sentry:s: 2 unresolved issues at error or above"
    assert result.examples == ("e", "f")


def test_titles_hold_the_source_key_and_a_count_never_alert_text():
    payloads = {
        "sentry": ({"type": "sentry", "name": "s", "organization": "acme", "token_env": "TEAM_KIT_ALERT_A"}, [{"level": "error", "title": "EVIL"}]),
        "pagerduty": ({"type": "pagerduty", "name": "p", "service_id": "P1", "token_env": "TEAM_KIT_ALERT_A"}, {"incidents": [{"status": "triggered", "title": "EVIL"}]}),
        "datadog": (datadog(), [{"name": "EVIL", "overall_state": "Alert"}]),
        "grafana": (grafana(), [{"state": "firing", "labels": {"alertname": "EVIL"}}]),
    }
    for source, payload in payloads.values():
        result, _ = poll_one(source, payload)
        assert "EVIL" not in result.title and result.examples == ("EVIL",)


def test_a_secret_echoed_in_an_alert_is_redacted_from_the_examples():
    result, _ = poll_one(datadog(), [{"name": "leak tok-a tok-b", "overall_state": "Alert"}])
    assert result.examples == ("leak [redacted] [redacted]",)


@pytest.mark.parametrize("error", [IncidentSourceError("sentry returned HTTP 503"), KeyError("x")])
def test_one_failing_source_is_a_failure_result_and_the_others_still_poll(error):
    def fetch(url, headers):
        if "sentry.io" in url:
            raise error
        return {"incidents": []}

    sources = [{"type": "sentry", "name": "s", "organization": "acme", "token_env": "TEAM_KIT_ALERT_A"},
               {"type": "pagerduty", "name": "p", "service_id": "P1", "token_env": "TEAM_KIT_ALERT_A"}]
    first, second = poll_sources(sources, fetch, ENV)
    assert isinstance(first, SourceFailure) and first.key == "sentry:s" and "tok-a" not in first.problem
    assert isinstance(second, ExternalObservation) and second.failing is False


def test_an_unset_token_variable_is_a_failure_result_not_an_abort():
    (result,) = poll_sources([datadog()], lambda *a: [], {"TEAM_KIT_ALERT_A": "x"})
    assert isinstance(result, SourceFailure) and "TEAM_KIT_ALERT_B is unset" in result.problem


@pytest.mark.parametrize("source", [
    datadog(token_env="GH_TOKEN"),
    datadog(app_key_env="GITHUB_TOKEN"),
    datadog(token_env="DATADOG_API_KEY"),
    grafana(token_env="GH_TOKEN"),
    datadog(url="https://evil.example"),
    datadog(site="evil.example"),
    datadog(url="https://api.datadoghq.com", site="datadoghq.com"),
    grafana(url="https://unlisted.example.com"),
    grafana(url="http://grafana.example.com"),
    grafana(path="//evil.example/x"),
    grafana(path="/api?x=1"),
    {"type": "pagerduty", "name": "p", "service_id": "P1", "token_env": "TEAM_KIT_ALERT_A", "url": "https://evil.example"},
])
def test_each_token_or_host_that_could_leak_a_credential_is_refused(source):
    with pytest.raises(IncidentSourceError):
        validate_sources([source], HOSTS)


@pytest.mark.parametrize("source", [
    datadog(),
    datadog(url="https://api.datadoghq.eu"),
    datadog(site="us5.datadoghq.com"),
    grafana(),
    {"type": "pagerduty", "name": "p", "service_id": "P1", "token_env": "TEAM_KIT_ALERT_A", "url": "https://api.pagerduty.com"},
    {"type": "sentry", "name": "s", "organization": "o", "token_env": "TEAM_KIT_ALERT_A", "url": "https://sentry.io"},
])
def test_pinned_and_listed_hosts_are_accepted(source):
    assert validate_sources([source], HOSTS) == [source]


def test_a_listed_custom_datadog_site_is_accepted_only_with_alert_hosts():
    custom = datadog(site="dd.corp.example")
    with pytest.raises(IncidentSourceError):
        validate_sources([custom], HOSTS)
    assert validate_sources([custom], {"api.dd.corp.example"}) == [custom]


def test_a_redirect_is_refused_so_a_token_header_never_follows_it():
    request = urllib.request.Request("https://sentry.io/x", headers={"Authorization": "Bearer tok"})
    assert _NoRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.example/") is None


def test_alert_hosts_in_guard_yaml_are_parsed_and_checked():
    policy = parse_policy(yaml.safe_load(guard_text()), BOTS)
    assert policy.alert_hosts == {"grafana.example.com"}
    plain = yaml.safe_load(guard_text())
    del plain["alert_hosts"]
    assert parse_policy(plain, BOTS).alert_hosts == frozenset()
    for bad in ("grafana.example.com", ["https://x.example"], ["x.example/path"], [3], ["nodots"]):
        plain["alert_hosts"] = bad
        with pytest.raises(PolicyError):
            parse_policy(plain, BOTS)
