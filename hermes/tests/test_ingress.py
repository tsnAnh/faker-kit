import hashlib
import hmac
import json
import os
import re
import secrets
import select
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import HERMES
from team_kit_event_queue import EventStore
from team_kit_ingress import ConfigError, IngressConfig, Limits, Route, load_config, make_server, read_secret_file

CLI = HERMES / "bin" / "team_kit_events.py"
PROJECTS = {"acme/shop": "shop", "acme/crm": "crm"}
HEAD = "a" * 40
FIXED_NOW = 1_700_000_000.0
MARKER = "PAYLOAD-MARKER-" + secrets.token_hex(8)
ROUTES = {"shop": ("/github/shop", "acme/shop"), "crm": ("/github/crm", "acme/crm")}
LOG_LINE = re.compile(r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00 (?:route=(?:-|[a-z0-9-]+) status=\d{3} delivery=(?:-|[A-Za-z0-9-]+) bytes=\d+|connection error)\Z")


def sign(raw, secret):
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()


def payload(repo="acme/shop", **extra):
    doc = {"action": "opened", "pull_request": {"number": 7, "title": MARKER, "head": {"sha": HEAD}}, "repository": {"full_name": repo, "id": 101},
           "sender": {"login": "octocat"}, **extra}
    return json.dumps(doc, separators=(",", ":")).encode()


def write_secret(path, value=None, mode=0o600):
    value = value if value is not None else secrets.token_hex(16).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value + b"\n")
    path.chmod(mode)
    return value


def headers_for(raw, secret, delivery="d-1", event="pull_request", signature=None):
    return [("X-GitHub-Event", event), ("X-GitHub-Delivery", delivery),
            ("X-Hub-Signature-256", sign(raw, secret) if signature is None else signature), ("Content-Type", "application/json")]


def request(path, body=b"", headers=(), *, method="POST", length=True):
    lines = [f"{method} {path} HTTP/1.1", "Host: localhost", *[f"{k}: {v}" for k, v in headers]]
    if length is True:
        lines.append(f"Content-Length: {len(body)}")
    elif length is not False:
        lines.append(f"Content-Length: {length}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


@dataclass
class Reply:
    status: int
    headers: dict
    body: bytes

    @property
    def doc(self):
        return json.loads(self.body)


def parse_reply(data):
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    return Reply(int(lines[0].split()[1]), {k.lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}, body)


def read_all(sock, wait):
    sock.settimeout(wait)
    data = b""
    while chunk := sock.recv(65536):
        data += chunk
    return data


def exchange(port, data, wait=5.0):
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as sock:
        sock.sendall(data)
        return parse_reply(read_all(sock, wait))


@dataclass
class Running:
    port: int
    db: Path
    log: list
    secrets: dict
    paths: dict
    config: IngressConfig
    sockets: list = field(default_factory=list)

    def post(self, name, body, delivery="d-1", event="pull_request", signature=None, secret=None, **kw):
        path = ROUTES[name][0]
        key = secret if secret is not None else self.secrets[name]
        return exchange(self.port, request(path, body, headers_for(body, key, delivery, event, signature), **kw))

    def rows(self):
        return rows(self.db)

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.sockets.append(sock)
        return sock


def rows(db, sql="SELECT delivery_id, body_sha256 FROM deliveries ORDER BY delivery_id"):
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def config_for(tmp_path, names=("shop",), limits=None, port=0):
    secret_values = {name: write_secret(tmp_path / "secrets" / f"{name}.secret") for name in names}
    routes = {ROUTES[name][0]: Route(ROUTES[name][0], ROUTES[name][1], name, tmp_path / "secrets" / f"{name}.secret") for name in names}
    return IngressConfig("127.0.0.1", port, routes, limits or Limits(read_timeout=2.0)), secret_values


@pytest.fixture
def start(tmp_path):
    started = []

    def launch(names=("shop",), limits=None, open_store=EventStore, now=lambda: FIXED_NOW):
        config, secret_values = config_for(tmp_path, names, limits)
        db = tmp_path / "state" / "events.sqlite3"
        log = []
        server = make_server(config, db, now=now, log=log.append, open_store=open_store)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        running = Running(server.bound_port, db, log, secret_values, {n: tmp_path / "secrets" / f"{n}.secret" for n in names}, config)
        started.append((server, thread, running))
        return running

    yield launch
    for server, thread, running in started:
        for sock in running.sockets:
            sock.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


# the brief's behaviors, in process over real sockets ---------------------------------------------------


def test_valid_signed_request_is_202_and_the_row_is_visible_from_a_second_connection(start):
    srv = start()
    body = payload()
    reply = srv.post("shop", body)
    assert (reply.status, reply.doc) == (202, {"status": 202, "reason": "recorded", "delivery": "d-1"})
    assert srv.rows() == [("d-1", hashlib.sha256(body).hexdigest())]
    assert reply.headers["content-type"] == "application/json"
    assert int(reply.headers["content-length"]) == len(reply.body)


def test_same_delivery_again_is_202_duplicate_and_one_row(start):
    srv = start()
    body = payload()
    assert srv.post("shop", body).status == 202
    again = srv.post("shop", body)
    assert (again.status, again.doc["reason"]) == (202, "duplicate delivery; already recorded")
    assert len(srv.rows()) == 1


def test_same_delivery_id_with_a_different_body_is_409_and_keeps_the_first(start):
    srv = start()
    first = payload()
    srv.post("shop", first)
    assert srv.post("shop", payload(action="closed")).status == 409
    assert srv.rows() == [("d-1", hashlib.sha256(first).hexdigest())]


@pytest.mark.parametrize("signature", ["sha256=" + "0" * 64, "sha1=" + "0" * 40, "", "garbage"], ids=["wrong", "wrong-algorithm", "empty", "garbage"])
def test_invalid_signature_is_401_and_records_nothing(start, signature):
    srv = start()
    assert srv.post("shop", payload(), signature=signature).status == 401
    assert srv.rows() == []


def test_an_unsigned_request_never_opens_the_queue(start):
    opened = []

    def spy(path):
        opened.append(path)
        return EventStore(path)

    srv = start(open_store=spy)
    assert srv.post("shop", payload(), signature="sha256=" + "0" * 64).status == 401
    assert opened == []
    assert srv.post("shop", payload()).status == 202
    assert len(opened) == 1


def test_missing_signature_is_401_and_records_nothing(start):
    srv = start()
    body = payload()
    reply = exchange(srv.port, request(ROUTES["shop"][0], body, [("X-GitHub-Event", "pull_request"), ("X-GitHub-Delivery", "d-1")]))
    assert reply.status == 401
    assert srv.rows() == []


def test_payload_for_another_repository_on_a_route_is_403_and_records_nothing(start):
    srv = start(("shop", "crm"))
    assert srv.post("shop", payload("acme/crm")).status == 403
    assert srv.rows() == []
    assert srv.post("crm", payload("acme/crm"), delivery="d-crm").status == 202
    assert [r[0] for r in srv.rows()] == ["d-crm"]


@pytest.mark.parametrize("path", ["/", "/github", "/github/shop/", "/github/shop?x=1", "/GITHUB/SHOP", "/github/crm"])
def test_unknown_path_is_404_and_records_nothing(start, path):
    srv = start()
    body = payload()
    reply = exchange(srv.port, request(path, body, headers_for(body, srv.secrets["shop"])))
    assert (reply.status, reply.doc["delivery"]) == (404, None)
    assert srv.rows() == []


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS", "FOO"])
def test_other_methods_are_405_with_allow_post(start, method):
    srv = start()
    reply = exchange(srv.port, request(ROUTES["shop"][0], b"", method=method, length=False))
    assert (reply.status, reply.headers["allow"]) == (405, "POST")
    assert srv.rows() == []


def test_put_with_a_body_is_405_and_the_client_reads_the_answer(start):
    srv = start()
    body = payload()
    reply = exchange(srv.port, request(ROUTES["shop"][0], body, headers_for(body, srv.secrets["shop"]), method="PUT"))
    assert reply.status == 405


def test_oversize_content_length_is_413_without_the_body_being_sent(start):
    srv = start(limits=Limits(max_body=1024, read_timeout=5.0))
    started = time.monotonic()
    reply = exchange(srv.port, request(ROUTES["shop"][0], b"", length=1025), wait=2.0)
    assert reply.status == 413
    assert time.monotonic() - started < 2.0
    assert srv.rows() == []


def test_body_of_exactly_max_body_is_read_not_refused(start):
    srv = start(limits=Limits(max_body=1024, read_timeout=2.0))
    reply = exchange(srv.port, request(ROUTES["shop"][0], b"x" * 1024, [("X-GitHub-Event", "ping"), ("X-GitHub-Delivery", "d-1")]))
    assert reply.status == 401


def test_missing_content_length_is_411(start):
    srv = start()
    assert exchange(srv.port, request(ROUTES["shop"][0], b"", length=False)).status == 411


def test_chunked_transfer_encoding_is_411(start):
    srv = start()
    body = payload()
    chunked = request(ROUTES["shop"][0], b"", [*headers_for(body, srv.secrets["shop"]), ("Transfer-Encoding", "chunked")], length=False)
    reply = exchange(srv.port, chunked + f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
    assert reply.status == 411
    assert srv.rows() == []


@pytest.mark.parametrize("length", ["abc", "-1", "+5", "0x10", "5 5", "", "1e3", "\u0665"], ids=lambda v: repr(v))
def test_malformed_content_length_is_400(start, length):
    srv = start()
    data = request(ROUTES["shop"][0], b"", length=False).replace(b"\r\n\r\n", f"\r\nContent-Length: {length}\r\n\r\n".encode())
    assert exchange(srv.port, data).status == 400


def test_huge_content_length_is_413_not_a_crash(start):
    srv = start()
    assert exchange(srv.port, request(ROUTES["shop"][0], b"", length="9" * 5000)).status == 413


@pytest.mark.parametrize("name", ["Content-Length", "X-Hub-Signature-256", "X-GitHub-Delivery", "X-GitHub-Event"])
def test_a_repeated_security_header_is_400_and_records_nothing(start, name):
    srv = start()
    body = payload()
    base = headers_for(body, srv.secrets["shop"])
    value = dict(base).get(name, str(len(body)))
    data = request(ROUTES["shop"][0], body, [*base, (name, value)])
    assert exchange(srv.port, data).status == 400
    assert srv.rows() == []


def test_content_length_larger_than_the_bytes_sent_is_408_at_the_deadline(start):
    srv = start(limits=Limits(read_timeout=0.5))
    body = payload()
    started = time.monotonic()
    reply = exchange(srv.port, request(ROUTES["shop"][0], body, headers_for(body, srv.secrets["shop"]), length=len(body) + 100), wait=4.0)
    assert reply.status == 408
    assert 0.3 < time.monotonic() - started < 3.0
    assert srv.rows() == []


def test_content_length_smaller_than_the_body_is_401_because_the_signature_covers_other_bytes(start):
    srv = start()
    body = payload()
    reply = exchange(srv.port, request(ROUTES["shop"][0], body, headers_for(body, srv.secrets["shop"]), length=len(body) - 5))
    assert reply.status == 401
    assert srv.rows() == []


def test_a_slow_client_connection_is_closed_with_nothing_or_a_408(start):
    srv = start(limits=Limits(read_timeout=0.5))
    slow = srv.connect()
    slow.sendall(b"POST /github/shop HTTP/1.1\r\nHost: localhost\r\nX-GitHub-Ev")
    started = time.monotonic()
    data = read_all(slow, 4.0)
    assert data == b"" or parse_reply(data).status == 408
    assert time.monotonic() - started < 3.0
    assert srv.post("shop", payload()).status == 202


def test_a_client_dribbling_bytes_cannot_outlast_the_deadline(start):
    srv = start(limits=Limits(read_timeout=0.6))
    slow = srv.connect()
    started = time.monotonic()
    closed = False
    try:
        for byte in b"POST /github/shop HTTP/1.1\r\nHost: localhost\r\nX-A: " + b"y" * 200:
            slow.sendall(bytes([byte]))
            time.sleep(0.2)
            if time.monotonic() - started > 4:
                break
    except OSError:
        closed = True
    assert closed or time.monotonic() - started < 4.5
    assert time.monotonic() - started < 4.5
    assert srv.post("shop", payload()).status == 202


class FailingCommit(EventStore):
    def _commit(self):
        raise sqlite3.OperationalError("disk I/O error")


def test_a_failing_commit_is_503_and_records_nothing(start, monkeypatch):
    srv = start()
    monkeypatch.setattr(EventStore, "_commit", FailingCommit._commit)
    reply = srv.post("shop", payload())
    assert reply.status == 503
    assert srv.rows() == []
    monkeypatch.undo()
    assert srv.post("shop", payload()).status == 202


def test_a_queue_that_cannot_be_opened_is_503(start):
    def broken(path):
        raise sqlite3.OperationalError("unable to open database file")

    srv = start(open_store=broken)
    reply = srv.post("shop", payload())
    assert (reply.status, reply.doc["delivery"]) == (503, None)
    assert srv.rows() == []


def test_a_queue_path_that_is_not_writable_is_503(start, tmp_path):
    srv = start()
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the queue's directory should be")
    broken = make_server(IngressConfig("127.0.0.1", 0, srv.config.routes, srv.config.limits), blocker / "events.sqlite3", now=lambda: FIXED_NOW, log=[].append)
    thread = threading.Thread(target=broken.serve_forever, daemon=True)
    thread.start()
    try:
        body = payload()
        reply = exchange(broken.bound_port, request(ROUTES["shop"][0], body, headers_for(body, srv.secrets["shop"])))
        assert reply.status == 503
    finally:
        broken.shutdown()
        broken.server_close()


def test_exact_bytes_are_signed_and_stored_whatever_their_whitespace_and_key_order(start):
    srv = start()
    body = b'\n {"sender" : {"login":"octocat"},\t"repository":{"id":101 ,"full_name":"acme/shop"},\r\n "pull_request":{"head":{"sha":"' + HEAD.encode() + b'"},"number":7} ,"action":"opened"}\n\n'
    assert srv.post("shop", body).status == 202
    assert srv.rows() == [("d-1", hashlib.sha256(body).hexdigest())]
    altered = body.replace(b"opened", b"openeD")
    assert srv.post("shop", altered, delivery="d-2", signature=sign(body, srv.secrets["shop"])).status == 401
    assert len(srv.rows()) == 1


def test_two_routes_with_different_secrets_do_not_accept_each_others_signatures(start):
    srv = start(("shop", "crm"))
    assert srv.secrets["shop"] != srv.secrets["crm"]
    assert srv.post("crm", payload("acme/shop"), secret=srv.secrets["shop"]).status == 401
    assert srv.post("shop", payload("acme/crm"), secret=srv.secrets["crm"]).status == 401
    assert srv.rows() == []
    assert srv.post("shop", payload("acme/shop")).status == 202
    assert srv.post("crm", payload("acme/crm"), delivery="d-2").status == 202


def test_rotating_the_secret_file_needs_no_restart(start):
    srv = start()
    body = payload()
    assert srv.post("shop", body).status == 202
    old = srv.secrets["shop"]
    srv.secrets["shop"] = write_secret(srv.paths["shop"])
    assert srv.post("shop", body, delivery="d-2", secret=old).status == 401
    assert srv.post("shop", body, delivery="d-3").status == 202


@pytest.mark.parametrize("damage", ["loosen", "delete", "empty"])
def test_a_secret_file_that_goes_bad_while_running_is_503_and_records_nothing(start, damage):
    srv = start()
    path = srv.paths["shop"]
    if damage == "loosen":
        path.chmod(0o640)
    elif damage == "delete":
        path.unlink()
    else:
        path.write_bytes(b" \n")
    reply = srv.post("shop", payload())
    assert (reply.status, reply.doc["reason"]) == (503, "route secret unavailable")
    assert srv.rows() == []
    if damage == "loosen":
        path.chmod(0o600)
        assert srv.post("shop", payload()).status == 202


@pytest.mark.parametrize("variant", ["too-many", "too-big", "long-request-line"])
def test_header_limits_answer_431_and_the_server_keeps_serving(start, variant):
    srv = start(limits=Limits(max_headers=8, max_header_bytes=1024, read_timeout=2.0))
    body = payload()
    good = headers_for(body, srv.secrets["shop"])
    path = ROUTES["shop"][0]
    if variant == "too-many":
        data = request(path, body, [*good, *[(f"X-Extra-{i}", "1") for i in range(10)]])
    elif variant == "too-big":
        data = request(path, body, [*good, ("X-Big", "y" * 2000)])
    else:
        data = request(path + "?" + "q" * 2000, body, good)
    reply = exchange(srv.port, data)
    assert reply.status == 431
    assert srv.rows() == []
    assert srv.post("shop", body).status == 202


def test_with_one_connection_allowed_a_second_gets_503_and_service_resumes(start):
    srv = start(limits=Limits(max_connections=1, read_timeout=1.0))
    held = srv.connect()
    held.sendall(b"POST /github/shop HTTP/1.1\r\nHost: localhost\r\n")
    time.sleep(0.2)
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", srv.port), timeout=3) as second:
        reply = parse_reply(read_all(second, 3.0))
    assert reply.status == 503
    assert "retry-after" in reply.headers
    assert time.monotonic() - started < 0.8
    held.close()
    deadline = time.monotonic() + 5
    while (answer := srv.post("shop", payload())).status == 503 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert answer.status == 202


def test_logs_have_only_the_documented_fields_and_leak_nothing(start):
    srv = start(("shop", "crm"))
    body = payload()
    srv.post("shop", body)
    srv.post("shop", body)
    srv.post("shop", payload(action="closed"))
    srv.post("shop", body, delivery="d-9", signature="sha256=" + "1" * 64)
    srv.post("shop", payload("acme/crm"), delivery="d-8")
    exchange(srv.port, request("/nope", body))
    exchange(srv.port, request(ROUTES["shop"][0], b"", method="GET", length=False))
    assert srv.log
    assert all(LOG_LINE.match(line) for line in srv.log), srv.log
    assert srv.log[0] == "2023-11-14T22:13:20+00:00 route=shop status=202 delivery=d-1 bytes=" + str(len(body))
    assert [line.split()[2] for line in srv.log] == ["status=202", "status=202", "status=409", "status=401", "status=403", "status=404", "status=405"]
    text = "\n".join(srv.log)
    for secret in srv.secrets.values():
        assert secret.decode() not in text
    for leaked in (MARKER, body.decode(), "sha256=", str(srv.paths["shop"]), "secrets", ROUTES["shop"][0]):
        assert leaked not in text


def test_a_connection_that_breaks_is_logged_as_one_line_without_a_traceback(start, capfd):
    srv = start()
    sock = srv.connect()
    sock.sendall(request(ROUTES["shop"][0], b"", length=5)[:-2])
    sock.close()
    time.sleep(0.3)
    assert capfd.readouterr().err == ""
    assert all(LOG_LINE.match(line) for line in srv.log)


# configuration --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.10", "10.0.0.1", "localhost", "example.com", "::", "", "127.0.0.1 "])
def test_a_non_loopback_bind_is_refused(tmp_path, bind):
    with pytest.raises(ConfigError) as err:
        IngressConfig(bind, 0, {}, Limits())
    assert "loopback" in err.value.problems[0] or "IP address literal" in err.value.problems[0]


@pytest.mark.parametrize("bind", ["127.0.0.1", "127.0.0.2", "::1"])
def test_loopback_addresses_are_accepted(bind):
    assert IngressConfig(bind, 0, {}, Limits()).bind == bind


def write_config(tmp_path, routes=None, **top):
    config = {"bind": "127.0.0.1", "port": 0, "routes": routes, **top}
    if routes is None:
        path, repo = ROUTES["shop"]
        config["routes"] = [{"path": path, "repository": repo, "secret_file": "secrets/shop.secret"}]
    target = tmp_path / "ingress.json"
    target.write_text(json.dumps({k: v for k, v in config.items() if v is not ...}))
    return target


def test_a_relative_secret_file_resolves_against_the_config_directory(tmp_path):
    write_secret(tmp_path / "secrets" / "shop.secret")
    config = load_config(write_config(tmp_path), PROJECTS)
    route = config.routes["/github/shop"]
    assert (route.repo, route.project, route.secret_file) == ("acme/shop", "shop", tmp_path.resolve() / "secrets" / "shop.secret")
    assert (config.bind, config.port, config.limits) == ("127.0.0.1", 0, Limits())


def test_limits_are_optional_and_each_may_be_set(tmp_path):
    write_secret(tmp_path / "secrets" / "shop.secret")
    given = {"max_body": 2048, "max_header_bytes": 4096, "max_headers": 10, "read_timeout": 1.5, "max_connections": 3}
    assert load_config(write_config(tmp_path, limits=given), PROJECTS).limits == Limits(2048, 4096, 10, 1.5, 3)
    assert load_config(write_config(tmp_path, limits={"max_body": 99}), PROJECTS).limits == Limits(max_body=99)


@pytest.mark.parametrize("limits", [
    {"max_body": 0}, {"max_body": -1}, {"max_body": (25 << 20) + 1}, {"max_body": "1"}, {"max_body": True}, {"max_body": 1.5},
    {"read_timeout": 0}, {"read_timeout": -1}, {"read_timeout": "5"}, {"read_timeout": 1000}, {"max_connections": 0}, {"max_headers": 0},
    {"max_headers": 101}, {"max_header_bytes": 0}, {"bogus": 1}, [],
], ids=repr)
def test_bad_limits_are_refused(tmp_path, limits):
    write_secret(tmp_path / "secrets" / "shop.secret")
    with pytest.raises(ConfigError) as err:
        load_config(write_config(tmp_path, limits=limits), PROJECTS)
    assert any("limits" in problem for problem in err.value.problems)


@pytest.mark.parametrize("top", [{"port": "80"}, {"port": -1}, {"port": 65536}, {"port": True}, {"port": ...}, {"bind": ...}, {"bind": 5}, {"bogus": 1}, {"routes": []}, {"routes": "x"}])
def test_bad_top_level_settings_are_refused(tmp_path, top):
    write_secret(tmp_path / "secrets" / "shop.secret")
    kwargs = dict(top)
    routes = kwargs.pop("routes", None)
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, routes, **kwargs), PROJECTS)


@pytest.mark.parametrize("route", [
    {"path": "github/shop", "repository": "acme/shop", "secret_file": "secrets/shop.secret"},
    {"path": "/x", "secret_file": "secrets/shop.secret"},
    {"path": "/x", "repository": "  ", "secret_file": "secrets/shop.secret"},
    {"path": "/x", "repository": "shop", "secret_file": "secrets/shop.secret"},
    {"path": "/x", "repository": "acme/unknown", "secret_file": "secrets/shop.secret"},
    {"path": "/x", "repository": "acme/shop"},
    {"path": "/x", "repository": "acme/shop", "secret_file": "secrets/shop.secret", "secret": "inline"},
    {"repository": "acme/shop", "secret_file": "secrets/shop.secret"},
    "route",
], ids=repr)
def test_bad_routes_are_refused(tmp_path, route):
    write_secret(tmp_path / "secrets" / "shop.secret")
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, [route]), PROJECTS)


def test_every_problem_is_collected_and_none_names_a_secret(tmp_path):
    value = write_secret(tmp_path / "secrets" / "shop.secret", mode=0o640)
    routes = [{"path": "/a", "repository": "acme/shop", "secret_file": "secrets/shop.secret"},
              {"path": "/a", "repository": "acme/shop", "secret_file": "secrets/missing.secret"},
              {"path": "/b", "secret_file": "secrets/shop.secret"}]
    with pytest.raises(ConfigError) as err:
        load_config(write_config(tmp_path, routes), PROJECTS)
    problems = err.value.problems
    assert any("0640" in p for p in problems)
    assert any("missing.secret" in p for p in problems)
    assert any("repeats path /a" in p for p in problems)
    assert any("repeats repository acme/shop" in p for p in problems)
    assert any("no repository" in p for p in problems)
    assert len(problems) >= 5
    assert value.decode() not in " ".join(problems)


def test_unreadable_and_invalid_config_files_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="missing or unreadable"):
        load_config(tmp_path / "none.json", PROJECTS)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(broken, PROJECTS)
    broken.write_text("[1]")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(broken, PROJECTS)


@pytest.mark.parametrize(("mode", "shown"), [(0o640, "0640"), (0o604, "0604"), (0o660, "0660"), (0o666, "0666"), (0o601, "0601")])
def test_a_secret_file_with_group_or_other_bits_is_refused_naming_file_and_mode(tmp_path, mode, shown):
    path = tmp_path / "s.secret"
    value = write_secret(path, mode=mode)
    with pytest.raises(ConfigError) as err:
        read_secret_file(path)
    assert err.value.problems == [f"secret file {path} is mode {shown}; it must not be group or world accessible (chmod 600)"]
    assert value.decode() not in str(err.value)


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_an_owner_only_secret_file_is_read_and_stripped(tmp_path, mode):
    path = tmp_path / "s.secret"
    path.write_bytes(b"  abc123 \n\n")
    path.chmod(mode)
    assert read_secret_file(path) == b"abc123"


@pytest.mark.parametrize("content", [b"", b"  \n\t\n"])
def test_an_empty_secret_file_is_refused(tmp_path, content):
    path = tmp_path / "s.secret"
    write_secret(path, content.strip())
    path.write_bytes(content)
    with pytest.raises(ConfigError, match="is empty"):
        read_secret_file(path)


def test_a_missing_secret_file_and_a_directory_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="missing or unreadable"):
        read_secret_file(tmp_path / "none")
    (tmp_path / "dir").mkdir(mode=0o700)
    with pytest.raises(ConfigError, match="not a regular file"):
        read_secret_file(tmp_path / "dir")


# the command, as a process --------------------------------------------------------------------------


@pytest.fixture
def cli_env(team_kit_home):
    return {**os.environ, "TEAM_KIT_HOME": str(team_kit_home)}


def cli(args, env, timeout=30):
    done = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, env=env, check=False, timeout=timeout, text=True)
    return done.returncode, done.stdout, done.stderr


def broken_config(tmp_path, case):
    if case == "mode-0640":
        write_secret(tmp_path / "secrets" / "shop.secret", mode=0o640)
        return write_config(tmp_path), "0640"
    if case == "mode-0604":
        write_secret(tmp_path / "secrets" / "shop.secret", mode=0o604)
        return write_config(tmp_path), "0604"
    if case == "missing-secret":
        return write_config(tmp_path), "shop.secret"
    if case == "empty-secret":
        write_secret(tmp_path / "secrets" / "shop.secret", b"")
        (tmp_path / "secrets" / "shop.secret").write_bytes(b"\n")
        return write_config(tmp_path), "is empty"
    write_secret(tmp_path / "secrets" / "shop.secret")
    write_secret(tmp_path / "secrets" / "crm.secret")
    shop = {"path": "/same", "repository": "acme/shop", "secret_file": "secrets/shop.secret"}
    if case == "duplicate-path":
        return write_config(tmp_path, [shop, {**shop, "repository": "acme/crm", "secret_file": "secrets/crm.secret"}]), "repeats path /same"
    if case == "no-repository":
        return write_config(tmp_path, [{"path": "/x", "secret_file": "secrets/shop.secret"}]), "no repository"
    if case == "not-a-project":
        return write_config(tmp_path, [{**shop, "repository": "acme/other"}]), "not a project in bots.yaml"
    raise AssertionError(case)


CASES = ["mode-0640", "mode-0604", "missing-secret", "empty-secret", "duplicate-path", "no-repository", "not-a-project"]


@pytest.mark.parametrize("command", ["check-config", "serve"])
@pytest.mark.parametrize("case", CASES)
def test_a_broken_config_fails_check_config_and_startup(cli_env, tmp_path, command, case):
    config, expected = broken_config(tmp_path, case)
    secret_bytes = [p.read_bytes().strip() for p in (tmp_path / "secrets").glob("*.secret")] if (tmp_path / "secrets").exists() else []
    code, out, err = cli([command, "--config", str(config)], cli_env)
    assert (code, out) == (1, "")
    assert "team-kit-events: refused: " in err
    assert expected in err
    assert all(line.startswith("team-kit-events: refused: ") for line in err.splitlines())
    assert all(value.decode() not in err for value in secret_bytes if value)


@pytest.mark.parametrize("command", ["check-config", "serve"])
@pytest.mark.parametrize("bind", ["0.0.0.0", "192.168.1.10", "localhost"])
def test_a_non_loopback_bind_fails_check_config_and_startup(cli_env, tmp_path, command, bind):
    write_secret(tmp_path / "secrets" / "shop.secret")
    config = write_config(tmp_path, bind=bind)
    code, out, err = cli([command, "--config", str(config)], cli_env)
    assert (code, out) == (1, "")
    assert "team-kit-events: refused: " in err
    assert "loopback" in err or "IP address literal" in err
    assert bind in err


@pytest.mark.parametrize("bind", ["::1", "127.0.0.2", "127.0.0.1"])
def test_check_config_accepts_loopback_binds_and_prints_the_routes_only(cli_env, tmp_path, bind):
    value = write_secret(tmp_path / "secrets" / "shop.secret")
    code, out, err = cli(["check-config", "--config", str(write_config(tmp_path, bind=bind))], cli_env)
    assert (code, err) == (0, "")
    assert json.loads(out) == {"ok": True, "routes": [{"path": "/github/shop", "repository": "acme/shop", "project": "shop"}]}
    assert value.decode() not in out


def test_check_config_without_a_config_option_is_a_usage_error(cli_env):
    assert cli(["check-config"], cli_env)[0] == 2


def start_cli(config, db, env):
    proc = subprocess.Popen([sys.executable, str(CLI), "serve", "--config", str(config), "--db", str(db)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
    ready, _, _ = select.select([proc.stderr], [], [], 20)
    assert ready, "serve printed nothing"
    line = proc.stderr.readline()
    match = re.fullmatch(r"team-kit-events: serving (\d+) route\(s\) on 127\.0\.0\.1:(\d+)\n", line)
    assert match, line
    return proc, int(match.group(2)), line


def stop_cli(proc):
    proc.send_signal(signal.SIGTERM)
    out, err = proc.communicate(timeout=10)
    return proc.returncode, out, err


def test_a_restart_on_the_same_database_keeps_earlier_deliveries_and_still_dedupes(cli_env, tmp_path):
    secret_one = write_secret(tmp_path / "secrets" / "shop.secret")
    config = write_config(tmp_path)
    db = tmp_path / "queue" / "events.sqlite3"
    first, second = payload(), payload(action="closed")
    texts = []

    proc, port, banner = start_cli(config, db, cli_env)
    texts.append(banner)
    assert exchange(port, request("/github/shop", first, headers_for(first, secret_one, "d-1"))).status == 202
    assert exchange(port, request("/github/shop", second, headers_for(second, secret_one, "d-2"))).status == 202
    code, out, err = stop_cli(proc)
    texts += [out, err]
    assert code == 0

    proc, port, banner = start_cli(config, db, cli_env)
    texts.append(banner)
    again = exchange(port, request("/github/shop", first, headers_for(first, secret_one, "d-1")))
    assert (again.status, again.doc["reason"]) == (202, "duplicate delivery; already recorded")
    assert exchange(port, request("/github/shop", second, headers_for(second, secret_one, "d-1"))).status == 409
    code, out, err = stop_cli(proc)
    texts += [out, err]
    assert code == 0

    assert rows(db) == [("d-1", hashlib.sha256(first).hexdigest()), ("d-2", hashlib.sha256(second).hexdigest())]
    combined = "".join(texts)
    for leaked in (secret_one.decode(), MARKER, first.decode(), "sha256="):
        assert leaked not in combined
    log_lines = [line for line in combined.splitlines() if "status=" in line]
    assert len(log_lines) == 4
    assert all(LOG_LINE.match(line) for line in log_lines), log_lines


def test_serve_exits_cleanly_on_sigterm_and_frees_the_port(cli_env, tmp_path):
    write_secret(tmp_path / "secrets" / "shop.secret")
    proc, port, _ = start_cli(write_config(tmp_path), tmp_path / "q.sqlite3", cli_env)
    assert stop_cli(proc)[0] == 0
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
