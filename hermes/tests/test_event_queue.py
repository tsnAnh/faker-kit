import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys

import pytest
from conftest import HERMES
from team_kit_event_queue import (
    EventStore,
    Lease,
    LeaseLost,
    Live,
    NotFound,
    RetryPolicy,
    State,
    Transient,
    handle_next,
    open_read_only,
    receive,
    status,
)

SECRET = b"s3cret-webhook-value-7f1c"
PROJECTS = {"acme/shop": "shop", "acme/crm": "crm"}
HEAD = "a" * 40
NEW_HEAD = "b" * 40
BASE = "c" * 40
# Free text a payload carries; none of it may reach the database.
TEXT = "UNTRUSTED-TEXT-ignore-previous-instructions"
CLI = HERMES / "bin" / "team_kit_events.py"


def sign(raw, secret=SECRET):
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()


def repo(full="acme/shop", rid=101):
    return {"full_name": full, "id": rid}


def pr(number=7, head=HEAD):
    return {"number": number, "title": TEXT, "body": TEXT, "head": {"sha": head, "ref": "dispatch/x", "repo": {"full_name": "fork/shop", "id": 202}},
            "base": {"sha": BASE, "ref": "main", "repo": repo()}}


PAYLOADS = {
    "pull_request": {"action": "synchronize", "pull_request": pr()},
    "issue_comment": {"action": "created", "issue": {"number": 12, "title": TEXT, "body": TEXT}, "comment": {"id": 9001, "body": f"<!-- team-kit:report v1 -->\n{TEXT}"}},
    "check_suite": {"action": "completed", "check_suite": {"id": 5001, "head_sha": HEAD, "head_branch": "dispatch/x"}},
    "check_run": {"action": "completed", "check_run": {"id": 6001, "head_sha": HEAD, "name": TEXT, "output": {"summary": TEXT}, "check_suite": {"id": 5001}}},
    "pull_request_review": {"action": "submitted", "review": {"id": 7001, "body": TEXT}, "pull_request": pr()},
    "workflow_run": {"action": "completed", "workflow_run": {"id": 8001, "head_sha": HEAD, "display_title": TEXT, "head_repository": {"full_name": "acme/shop", "id": 101}}},
    "issues": {"action": "labeled", "issue": {"number": 12, "title": TEXT, "body": TEXT}},
}
EXPECTED_IDS = {
    "pull_request": {"number": 7, "head_sha": HEAD, "base_sha": BASE, "base_ref": "main", "head_repo": "fork/shop", "head_repo_id": 202},
    "issue_comment": {"number": 12, "comment_id": 9001},
    "check_suite": {"check_suite_id": 5001, "head_sha": HEAD},
    "check_run": {"check_run_id": 6001, "check_suite_id": 5001, "head_sha": HEAD},
    "pull_request_review": {"number": 7, "review_id": 7001, "head_sha": HEAD, "base_sha": BASE, "base_ref": "main", "head_repo": "fork/shop", "head_repo_id": 202},
    "workflow_run": {"workflow_run_id": 8001, "head_sha": HEAD, "head_repo": "acme/shop", "head_repo_id": 101},
    "issues": {"number": 12},
}
ID_COLUMNS = ("number", "comment_id", "review_id", "check_run_id", "check_suite_id", "workflow_run_id", "head_sha", "base_sha", "base_ref", "head_repo", "head_repo_id")


def body(event="pull_request", full="acme/shop", **extra):
    payload = {**PAYLOADS.get(event, {}), "repository": repo(full), "sender": {"login": "octocat"}, **extra}
    return json.dumps(payload, separators=(",", ":")).encode()


def headers(raw, event="pull_request", delivery="d-1", signature=None):
    return {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": sign(raw) if signature is None else signature}


@pytest.fixture
def db(tmp_path):
    return tmp_path / "state" / "events" / "events.sqlite3"


@pytest.fixture
def store(db):
    s = EventStore(db)
    yield s
    s.close()


def rows(db, table="deliveries"):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def post(store, raw, now=1000.0, **kw):
    return receive(raw, headers(raw, **kw), SECRET, PROJECTS, store, now)


# receiver ---------------------------------------------------------------------------------------


def test_valid_signature_answers_202_after_the_row_is_committed(store, db):
    raw = body()
    answer = post(store, raw)
    assert (answer.status, answer.delivery_id, answer.shadow_check) == (202, "d-1", "team-kit-tick")
    with sqlite3.connect(db) as other:
        assert other.execute("SELECT delivery_id, project, repo, repo_id, event, action, sender, body_sha256 FROM deliveries").fetchall() == [
            ("d-1", "shop", "acme/shop", 101, "pull_request", "synchronize", "octocat", hashlib.sha256(raw).hexdigest())]
    assert oct(db.stat().st_mode & 0o777) == "0o600"
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.parametrize("signature", [
    "",
    "sha256=" + "0" * 64,
    "sha1=" + "0" * 40,
    sign(body())[:-2],
    sign(body(), secret=b"wrong-secret"),
    sign(body() + b" "),
])
def test_bad_signature_answers_401_and_stores_nothing(store, db, signature):
    raw = body()
    answer = receive(raw, headers(raw, signature=signature), SECRET, PROJECTS, store, 1000.0)
    assert answer.status == 401
    assert rows(db) == 0


def test_missing_signature_header_answers_401(store, db):
    raw = body()
    answer = receive(raw, {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "d-1"}, SECRET, PROJECTS, store, 1000.0)
    assert answer.status == 401
    assert rows(db) == 0


def test_body_modified_after_signing_answers_401(store, db):
    raw = body()
    tampered = raw.replace(b"synchronize", b"closed")
    assert receive(tampered, headers(raw), SECRET, PROJECTS, store, 1000.0).status == 401
    assert rows(db) == 0


def test_signature_over_reserialized_json_is_rejected(store, db):
    raw = body()
    reserialized = json.dumps(json.loads(raw), indent=2).encode()
    assert receive(raw, headers(raw, signature=sign(reserialized)), SECRET, PROJECTS, store, 1000.0).status == 401
    assert rows(db) == 0


def test_repository_outside_bots_yaml_is_refused_and_stores_nothing(store, db):
    raw = body(full="evil/shop")
    answer = post(store, raw)
    assert (answer.status, answer.reason) == (403, "repository is not a project in bots.yaml")
    assert rows(db) == 0
    assert rows(db, "consumer_state") == 0


def test_duplicate_delivery_with_same_body_is_one_row_and_202(store, db):
    raw = body()
    assert post(store, raw).status == 202
    again = post(store, raw, now=2000.0)
    assert (again.status, again.reason) == (202, "duplicate delivery; already recorded")
    assert rows(db) == 1
    assert rows(db, "consumer_state") == 1


def test_duplicate_delivery_with_different_body_is_a_conflict_that_keeps_the_first(store, db):
    first = body()
    assert post(store, first).status == 202
    answer = post(store, body(action="closed"))
    assert answer.status == 409
    assert store.delivery("d-1").body_sha256 == hashlib.sha256(first).hexdigest()
    assert rows(db) == 1
    assert status(open_read_only(db))["shop"]["conflicts"] == 1


class FailingCommit(EventStore):
    def _commit(self):
        raise sqlite3.OperationalError("disk I/O error")


def test_failed_commit_answers_503_and_leaves_no_row(db):
    failing = FailingCommit(db)
    answer = post(failing, body())
    failing.close()
    assert answer.status == 503
    assert rows(db) == 0
    again = EventStore(db)
    assert post(again, body()).status == 202
    again.close()


def test_failed_insert_answers_503(store, db):
    store.conn.execute("DROP TABLE consumer_state")
    assert post(store, body()).status == 503
    assert rows(db) == 0


def test_dedupe_survives_a_new_store(db):
    first = EventStore(db)
    assert post(first, body()).status == 202
    first.close()
    second = EventStore(db)
    assert post(second, body()).reason == "duplicate delivery; already recorded"
    assert post(second, body(action="closed")).status == 409
    second.close()
    assert rows(db) == 1


# envelope ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("event", sorted(PAYLOADS))
def test_typed_identifiers_are_stored_and_read_back_after_restart_without_the_body(db, event):
    first = EventStore(db)
    assert post(first, body(event), event=event, delivery=f"d-{event.replace('_', '-')}").status == 202
    first.close()
    again = EventStore(db)
    stored = again.delivery(f"d-{event.replace('_', '-')}")
    again.close()
    assert {c: getattr(stored, c) for c in ID_COLUMNS if getattr(stored, c) is not None} == EXPECTED_IDS[event]
    assert stored.needs_reconcile is False
    assert_no_text(db)


def assert_no_text(db):
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        for table in ("deliveries", "consumer_state", "conflicts", "project_locks"):
            for row in conn.execute(f"SELECT * FROM {table}"):
                assert not any(isinstance(v, str) and ("UNTRUSTED" in v or "team-kit:report" in v) for v in row)
    for path in db.parent.iterdir():
        assert b"UNTRUSTED" not in path.read_bytes()


def test_report_comment_is_located_again_after_restart(db):
    first = EventStore(db)
    post(first, body("issue_comment"), event="issue_comment", delivery="report-1")
    first.close()
    again = EventStore(db)
    lease = again.claim("lifecycle", "shop", now=2000.0)
    found = again.delivery(lease.delivery_id)
    again.close()
    assert (found.delivery_id, found.event, found.number, found.comment_id) == ("report-1", "issue_comment", 12, 9001)


def test_event_without_an_identifier_is_recorded_for_reconciliation_not_consumed(store, db):
    raw = body("issues", issue={"title": TEXT})
    assert post(store, raw, event="issues", delivery="no-ids").status == 202
    assert store.delivery("no-ids").needs_reconcile is True
    assert store.consumer_row("no-ids", "lifecycle").state is State.RECONCILE
    assert store.claim("lifecycle", "shop", now=5000.0) is None
    assert status(open_read_only(db))["shop"]["needs_reconcile"] == 1


def test_event_no_consumer_takes_is_still_recorded(store):
    assert post(store, body("ping", zen="x"), event="ping", delivery="ping-1").shadow_check == "none"
    assert store.delivery("ping-1").needs_reconcile is True
    assert store.consumer_row("ping-1", "lifecycle") is None


def test_push_goes_to_the_security_consumer(store):
    raw = body("push", after=HEAD, before=BASE, ref="refs/heads/main")
    assert post(store, raw, event="push", delivery="push-1").shadow_check == "team-kit-security"
    assert store.consumer_row("push-1", "security").state is State.PENDING
    assert store.consumer_row("push-1", "lifecycle") is None


# consumers, retries, fencing ---------------------------------------------------------------------

BOTH = {"lifecycle": frozenset({"pull_request"}), "security": frozenset({"pull_request"})}


def test_two_consumers_each_claim_a_delivery_once(db):
    store = EventStore(db, consumers=BOTH)
    post(store, body())
    first, second = store.claim("lifecycle", "shop", 1001.0), store.claim("security", "shop", 1001.0)
    assert (first.delivery_id, second.delivery_id) == ("d-1", "d-1")
    assert store.claim("lifecycle", "shop", 1002.0) is None
    store.complete(first, 1002.0)
    assert store.consumer_row("d-1", "security").state is State.CLAIMED
    store.complete(second, 1003.0)
    assert store.claim("lifecycle", "shop", 1004.0) is None
    assert store.claim("security", "shop", 1004.0) is None
    store.close()


def test_retries_back_off_on_the_injected_clock_and_end_dead(db):
    store = EventStore(db, retry=RetryPolicy(base=30, cap=100, max_attempts=4))
    post(store, body())
    now, waits = 1000.0, []
    for _ in range(3):
        lease = store.claim("lifecycle", "shop", now)
        assert store.retry(lease, now, "boom") is State.RETRYING
        row = store.consumer_row("d-1", "lifecycle")
        waits.append(row.next_at - now)
        assert store.claim("lifecycle", "shop", row.next_at - 1) is None
        now = row.next_at
    assert waits == [30, 60, 100]
    assert store.retry(store.claim("lifecycle", "shop", now), now, "boom") is State.DEAD
    assert store.consumer_row("d-1", "lifecycle").attempts == 4
    assert store.claim("lifecycle", "shop", now + 10**6) is None
    assert store.consumer_row("d-1", "lifecycle").state is State.DEAD
    store.close()


def test_project_lock_serializes_workers(store):
    token = store.acquire_project_lock("shop", "worker-a", 1000.0, ttl=60)
    assert token
    assert store.acquire_project_lock("shop", "worker-b", 1010.0) is None
    assert store.acquire_project_lock("crm", "worker-b", 1010.0)
    post(store, body())
    assert handle_next(store, "lifecycle", "shop", fetch=None, transition=None, now=1020.0, holder="worker-b").state == "locked"
    assert store.release_project_lock("shop", "not-the-token") is False
    assert store.release_project_lock("shop", token) is True
    assert store.acquire_project_lock("shop", "worker-b", 1030.0)
    assert store.acquire_project_lock("shop", "worker-c", 1030.0 + 301) is not None


def test_stale_lease_cannot_settle_a_newer_claim(store):
    post(store, body())
    stale = store.claim("lifecycle", "shop", 1000.0, lease_seconds=10)
    fresh = store.claim("lifecycle", "shop", 1011.0, lease_seconds=10)
    assert fresh.generation == stale.generation + 2
    for settle in (lambda: store.complete(stale, 1012.0), lambda: store.fail(stale, 1012.0, "x"), lambda: store.retry(stale, 1012.0, "x")):
        with pytest.raises(LeaseLost):
            settle()
    old_generation = Lease(fresh.delivery_id, fresh.consumer, fresh.token, fresh.generation - 1, fresh.expires)
    with pytest.raises(LeaseLost):
        store.complete(old_generation, 1012.0)
    assert store.consumer_row("d-1", "lifecycle").state is State.CLAIMED
    assert store.complete(fresh, 1012.0) is State.DONE
    with pytest.raises(LeaseLost):
        store.complete(fresh, 1013.0)


def test_lease_past_its_expiry_cannot_settle_even_before_a_new_claim(store):
    post(store, body())
    lease = store.claim("lifecycle", "shop", 1000.0, lease_seconds=10)
    with pytest.raises(LeaseLost):
        store.complete(lease, 1010.0)


def test_expired_lease_returns_to_pending_with_the_generation_advanced(store):
    post(store, body())
    lease = store.claim("lifecycle", "shop", 1000.0, lease_seconds=10)
    assert store.expire_leases(1005.0) == 0
    assert store.expire_leases(1010.0) == 1
    row = store.consumer_row("d-1", "lifecycle")
    assert (row.state, row.generation) == (State.PENDING, lease.generation + 1)
    again = store.claim("lifecycle", "shop", 1011.0)
    assert (again.delivery_id, again.generation) == ("d-1", lease.generation + 2)


def test_two_consumers_fence_independently(db):
    store = EventStore(db, consumers=BOTH)
    post(store, body())
    life, sec = store.claim("lifecycle", "shop", 1000.0), store.claim("security", "shop", 1000.0)
    borrowed = Lease(sec.delivery_id, "security", life.token, life.generation, life.expires)
    with pytest.raises(LeaseLost):
        store.complete(borrowed, 1001.0)
    store.complete(life, 1001.0)
    assert store.consumer_row("d-1", "security").state is State.CLAIMED
    assert store.complete(sec, 1001.0) is State.DONE
    store.close()


# the consumer contract, with an injected fetcher -------------------------------------------------------


class Remote:
    """Stands in for GitHub: the live objects and the side effects a transition makes on them."""

    def __init__(self, head=HEAD):
        self.head = head
        self.labels = []
        self.reads = 0
        self.failures = []

    def fetch(self, delivery):
        self.reads += 1
        if self.failures:
            raise self.failures.pop(0)
        return Live(repo=delivery.repo, repo_id=delivery.repo_id, number=delivery.number, head_sha=self.head)


class LabelOnce:
    def __init__(self, remote, crash=False):
        self.remote, self.crash = remote, crash

    def gate(self, delivery, live):
        return None

    def done(self, delivery, live):
        return "checked" in self.remote.labels

    def apply(self, delivery, live):
        self.remote.labels.append("checked")
        if self.crash:
            raise Crash


class Crash(BaseException):
    pass


def run(store, remote, now, transition=None):
    return handle_next(store, "lifecycle", "shop", remote.fetch, transition or LabelOnce(remote), now, holder="w", lease_seconds=60)


def test_transient_read_failure_retries_with_backoff(store):
    post(store, body())
    remote = Remote()
    remote.failures = [Transient("502"), Transient("timeout")]
    assert run(store, remote, 1000.0).state == "retrying"
    assert run(store, remote, 1001.0).state == "idle"
    assert run(store, remote, 1030.0).state == "retrying"
    assert remote.labels == []
    assert run(store, remote, 1090.0).state == "done"
    assert remote.labels == ["checked"]


def test_confirmed_not_found_ends_gone_not_done(store, db):
    post(store, body())
    remote = Remote()
    remote.failures = [NotFound("404")] * 5
    now, outcomes = 1000.0, []
    while (out := run(store, remote, now)).state != "idle":
        outcomes.append(out.state)
        now += 10**4
    assert outcomes == ["retrying"] * 4 + ["gone"]
    assert store.consumer_row("d-1", "lifecycle").reason == "not found: 404"
    assert remote.labels == []
    assert status(open_read_only(db))["shop"]["consumers"]["lifecycle"] == {"gone": 1}


def test_moved_head_ends_superseded_not_done(store):
    post(store, body())
    remote = Remote(head=NEW_HEAD)
    out = run(store, remote, 1000.0)
    assert (out.state, out.reason) == ("superseded", f"head moved from {HEAD} to {NEW_HEAD}")
    assert store.consumer_row("d-1", "lifecycle").state is State.SUPERSEDED
    assert remote.labels == []


def test_live_object_in_another_repository_is_a_dead_letter(store):
    post(store, body())

    def elsewhere(delivery):
        return Live(repo="evil/shop", repo_id=999, number=delivery.number, head_sha=HEAD)

    assert handle_next(store, "lifecycle", "shop", elsewhere, LabelOnce(Remote()), 1000.0, holder="w").state == "dead"


def test_crash_after_side_effect_rehandles_with_no_second_effect(store):
    post(store, body())
    remote = Remote()
    with pytest.raises(Crash):
        run(store, remote, 1000.0, LabelOnce(remote, crash=True))
    assert remote.labels == ["checked"]
    assert store.consumer_row("d-1", "lifecycle").state is State.CLAIMED
    assert run(store, remote, 1030.0).state == "idle"
    assert run(store, remote, 1061.0).state == "done"
    assert remote.labels == ["checked"]


# the command ----------------------------------------------------------------------------------------


def request_bytes(raw, **kw):
    lines = [f"{k}: {v}" for k, v in headers(raw, **kw).items()]
    return ("POST /webhooks HTTP/1.1\r\n" + "\r\n".join([*lines, "Content-Type: application/json"]) + "\r\n\r\n").encode() + raw


def cli(args, env, stdin=b""):
    done = subprocess.run([sys.executable, str(CLI), *args], input=stdin, capture_output=True, env=env, check=False)
    return done.returncode, done.stdout.decode(), done.stderr.decode()


@pytest.fixture
def cli_env(team_kit_home, tmp_path):
    spy = tmp_path / "spy"
    spy.mkdir()
    for name in ("gh", "hermes", "git", "claude", "codex"):
        tool = spy / name
        tool.write_text(f"#!/bin/sh\necho {name} \"$@\" >> {spy / 'calls.log'}\n")
        tool.chmod(0o755)
    return {**os.environ, "TEAM_KIT_HOME": str(team_kit_home), "PATH": f"{spy}:{os.environ['PATH']}", "TEAM_KIT_WEBHOOK_SECRET": SECRET.decode()}


def test_cli_records_in_shadow_mode_dedupes_across_processes_and_never_leaks_the_secret(cli_env, team_kit_home, tmp_path):
    raw = body("pull_request_review")
    request = request_bytes(raw, event="pull_request_review", delivery="d-cli")
    argv = ["receive", "--request", "-", "--secret-env", "TEAM_KIT_WEBHOOK_SECRET"]
    code, out, err = cli(argv, cli_env, request)
    assert code == 0, err
    assert json.loads(out) == {"status": 202, "reason": "recorded", "delivery": "d-cli", "shadow_check": "team-kit-tick"}
    code, out, err = cli(argv, cli_env, request)
    assert (code, json.loads(out)["reason"]) == (0, "duplicate delivery; already recorded")
    bad = request_bytes(raw, event="pull_request_review", delivery="d-bad", signature=sign(raw, b"nope"))
    code, out, err = cli(argv, cli_env, bad)
    assert (code, json.loads(out)["status"]) == (1, 401)
    db = team_kit_home / "events" / "events.sqlite3"
    assert rows(db) == 1
    assert oct(db.stat().st_mode & 0o777) == "0o600"
    for text in (out, err, " ".join(argv)):
        assert SECRET.decode() not in text
    assert all(SECRET not in p.read_bytes() for p in db.parent.iterdir())
    assert not (tmp_path / "spy" / "calls.log").exists()
    assert sorted(p.name for p in team_kit_home.iterdir()) == ["bots.yaml", "events", "guard.yaml"]


def test_cli_reads_the_secret_from_a_file_and_the_request_from_a_file(cli_env, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text(SECRET.decode() + "\n")
    raw = body()
    (tmp_path / "req").write_bytes(request_bytes(raw).replace(b"\r\n", b"\n"))
    code, out, err = cli(["receive", "--request", str(tmp_path / "req"), "--secret-file", str(secret)], cli_env)
    assert (code, json.loads(out)["status"]) == (0, 202), err


def test_cli_refuses_an_unset_secret_without_naming_a_value(cli_env):
    env = {k: v for k, v in cli_env.items() if k != "TEAM_KIT_WEBHOOK_SECRET"}
    code, out, err = cli(["receive", "--request", "-", "--secret-env", "TEAM_KIT_WEBHOOK_SECRET"], env, request_bytes(body()))
    assert (code, out) == (1, "")
    assert err == "team-kit-events: refused: environment variable TEAM_KIT_WEBHOOK_SECRET is empty or unset\n"


def test_status_and_dead_letters_are_read_only_and_print_no_token(cli_env, team_kit_home):
    db = team_kit_home / "events" / "events.sqlite3"
    store = EventStore(db, retry=RetryPolicy(max_attempts=1))
    post(store, body())
    post(store, body(action="closed"))
    post(store, body("push", after=HEAD, before=BASE, ref="refs/heads/main"), event="push", delivery="p-1")
    post(store, body("issues", full="acme/crm"), event="issues", delivery="c-1")
    lease = store.claim("lifecycle", "shop", 2000.0)
    store.retry(lease, 2000.0, "transient: 502")
    held = store.claim("security", "shop", 2000.0)
    store.close()
    before = db.read_bytes()
    code, out, err = cli(["status"], cli_env)
    assert code == 0, err
    assert json.loads(out) == {
        "crm": {"conflicts": 0, "consumers": {"lifecycle": {"pending": 1}}, "dead_letters": 0, "deliveries": 1, "needs_reconcile": 0, "queue_depth": 1, "retries": 0},
        "shop": {"conflicts": 1, "consumers": {"lifecycle": {"dead": 1}, "security": {"claimed": 1}}, "dead_letters": 1, "deliveries": 2,
                 "needs_reconcile": 0, "queue_depth": 1, "retries": 1},
    }
    code, out, err = cli(["dead-letters", "--project", "shop"], cli_env)
    assert json.loads(out) == [{"project": "shop", "consumer": "lifecycle", "delivery_id": "d-1", "event": "pull_request", "action": "synchronize",
                                "number": 7, "head_sha": HEAD, "attempts": 1, "reason": "transient: 502", "dead_since": 2000.0}]
    assert held.token not in out
    assert json.loads(cli(["status", "--project", "crm"], cli_env)[1]).keys() == {"crm"}
    assert db.read_bytes() == before


def test_status_with_no_queue_yet_is_empty(cli_env):
    assert cli(["status"], cli_env)[:2] == (0, "{}\n")
