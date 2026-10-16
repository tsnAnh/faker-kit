"""A durable, authenticated queue of GitHub webhook deliveries, in shadow mode.

The receiver handles one request in a fixed order: verify the HMAC signature over the original raw bytes, parse,
bind the repository to a project in bots.yaml, insert one row keyed by the unique delivery id and commit, and only
then answer 202. A failed insert or commit answers 503 so GitHub redelivers. Nothing is coalesced or dropped before
the commit.

A row keeps typed pointers (issue or pull request number, comment, review, check-run, check-suite and workflow-run
ids, head and base, the head repository), never a body or any issue or comment text. A consumer re-fetches what a
pointer names through a trusted read and checks it against live data before it acts.

The guarantee is that each delivery is recorded once and handled logically once per consumer. It is not exactly-once
for remote side effects: a crash after a side effect and before `complete` handles the row again, so every transition
checks the live state first (`Transition.done`) and skips what already happened.

Shadow mode is the only mode. A row records the job its event would trigger (`shadow_check`); nothing here changes a
label, launches, merges, or closes. No model runs in this path, and nothing reads a Hermes database.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from team_kit_machine import ROUTE_JOBS

SIGNATURE = re.compile(r"\Asha256=[0-9a-f]{64}\Z")
DELIVERY_ID = re.compile(r"\A[A-Za-z0-9-]{1,64}\Z")
EVENT_NAME = re.compile(r"\A[a-z_]{1,64}\Z")
SHA = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

# Which job each event would trigger today, from the routes setup-machine.sh registers. Recorded, never run.
SHADOW_CHECKS = {event: job for job, events in ROUTE_JOBS.values() for event in events}

# Each consumer keeps its own state row per delivery, so one finishing never hides a delivery from another.
CONSUMERS: Mapping[str, frozenset[str]] = {
    "lifecycle": frozenset(ROUTE_JOBS["tick"][1]) | {"pull_request_review_comment", "check_run", "workflow_run"},
    "security": frozenset(ROUTE_JOBS["security"][1]),
}

_PR = {"number": ("pull_request", "number"), "head_sha": ("pull_request", "head", "sha"), "base_sha": ("pull_request", "base", "sha"),
       "base_ref": ("pull_request", "base", "ref"), "head_repo": ("pull_request", "head", "repo", "full_name"),
       "head_repo_id": ("pull_request", "head", "repo", "id")}
# Where each event carries each typed identifier. An event not listed here carries none.
IDENTIFIER_PATHS: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "pull_request": _PR,
    "pull_request_review": {**_PR, "review_id": ("review", "id")},
    "pull_request_review_comment": {**_PR, "comment_id": ("comment", "id")},
    "issues": {"number": ("issue", "number")},
    "issue_comment": {"number": ("issue", "number"), "comment_id": ("comment", "id")},
    "check_suite": {"check_suite_id": ("check_suite", "id"), "head_sha": ("check_suite", "head_sha")},
    "check_run": {"check_run_id": ("check_run", "id"), "check_suite_id": ("check_run", "check_suite", "id"), "head_sha": ("check_run", "head_sha")},
    "workflow_run": {"workflow_run_id": ("workflow_run", "id"), "head_sha": ("workflow_run", "head_sha"),
                     "head_repo": ("workflow_run", "head_repository", "full_name"), "head_repo_id": ("workflow_run", "head_repository", "id")},
    "push": {"head_sha": ("after",), "base_sha": ("before",), "base_ref": ("ref",)},
}
INT_IDS = ("number", "comment_id", "review_id", "check_run_id", "check_suite_id", "workflow_run_id", "head_repo_id")
TEXT_IDS = ("head_sha", "base_sha", "base_ref", "head_repo")
# Any one of these locates the object again; without one the event needs repository-level reconciliation.
LOCATING_IDS = ("number", "comment_id", "review_id", "check_run_id", "check_suite_id", "workflow_run_id", "head_sha")


class State(enum.Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRYING = "retrying"
    DONE = "done"
    SUPERSEDED = "superseded"
    GONE = "gone"
    DEAD = "dead"
    RECONCILE = "reconcile"




@dataclass(frozen=True)
class Envelope:
    """What one delivery holds once verified and bound: identity, digest, and pointers. Never its body."""

    delivery_id: str
    project: str
    repo: str
    repo_id: int | None
    event: str
    action: str | None
    sender: str | None
    body_sha256: str
    received_at: float
    ids: Mapping[str, int | str]
    shadow_check: str

    @property
    def needs_reconcile(self) -> bool:
        return not any(key in self.ids for key in LOCATING_IDS)


@dataclass(frozen=True)
class Delivery:
    """A stored row, read back without its body."""

    delivery_id: str
    project: str
    repo: str
    repo_id: int | None
    event: str
    action: str | None
    sender: str | None
    body_sha256: str
    received_at: float
    number: int | None
    comment_id: int | None
    review_id: int | None
    check_run_id: int | None
    check_suite_id: int | None
    workflow_run_id: int | None
    head_sha: str | None
    base_sha: str | None
    base_ref: str | None
    head_repo: str | None
    head_repo_id: int | None
    needs_reconcile: bool
    shadow_check: str


@dataclass(frozen=True)
class Lease:
    delivery_id: str
    consumer: str
    token: str
    generation: int
    expires: float


@dataclass(frozen=True)
class ConsumerRow:
    state: State
    attempts: int
    next_at: float
    generation: int
    reason: str | None


@dataclass(frozen=True)
class RetryPolicy:
    base: float = 30.0
    cap: float = 3600.0
    max_attempts: int = 5

    def delay(self, attempts: int) -> float:
        return float(min(self.base * 2 ** (attempts - 1), self.cap))


@dataclass(frozen=True)
class Response:
    status: int
    reason: str
    delivery_id: str | None = None
    shadow_check: str | None = None


class Recorded(enum.Enum):
    INSERTED = "inserted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class LeaseLost(RuntimeError):
    """The lease is stale: another claim, an expiry, or a settled row replaced it."""


def _at(payload: object, path: tuple[str, ...]) -> object:
    for key in path:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload


def _identifiers(event: str, payload: object) -> dict[str, int | str]:
    ids: dict[str, int | str] = {}
    for column, path in IDENTIFIER_PATHS.get(event, {}).items():
        value = _at(payload, path)
        if column in INT_IDS and isinstance(value, int) and not isinstance(value, bool) and value > 0:
            ids[column] = value
        elif column in TEXT_IDS and isinstance(value, str) and value and len(value) <= 255:
            if column in ("head_sha", "base_sha") and not SHA.match(value):
                continue
            ids[column] = value
    return ids


def verify_signature(raw: bytes, header: str | None, secret: bytes) -> bool:
    """HMAC-SHA256 over the original bytes, compared in constant time."""
    if not secret or not header or not SIGNATURE.match(header):
        return False
    expected = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    return next((value.strip() for key, value in headers.items() if key.lower() == wanted), None)


def envelope(raw: bytes, headers: Mapping[str, str], projects: Mapping[str, str], now: float) -> Envelope | Response:
    """Parse a verified request and bind it to a project. Returns a refusal Response when it cannot be recorded."""
    delivery_id = _header(headers, "X-GitHub-Delivery") or ""
    event = _header(headers, "X-GitHub-Event") or ""
    if not DELIVERY_ID.match(delivery_id):
        return Response(400, "missing or malformed X-GitHub-Delivery")
    if not EVENT_NAME.match(event):
        return Response(400, "missing or malformed X-GitHub-Event", delivery_id)
    try:
        payload: object = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Response(400, "body is not JSON", delivery_id)
    repo = _at(payload, ("repository", "full_name"))
    project = projects.get(repo.lower()) if isinstance(repo, str) else None
    if not isinstance(repo, str) or project is None:
        return Response(403, "repository is not a project in bots.yaml", delivery_id)
    repo_id, action, sender = _at(payload, ("repository", "id")), _at(payload, ("action",)), _at(payload, ("sender", "login"))
    return Envelope(
        delivery_id=delivery_id, project=project, repo=repo, event=event,
        repo_id=repo_id if isinstance(repo_id, int) and not isinstance(repo_id, bool) else None,
        action=action if isinstance(action, str) else None, sender=sender if isinstance(sender, str) else None,
        body_sha256=hashlib.sha256(raw).hexdigest(), received_at=now, ids=_identifiers(event, payload),
        shadow_check=SHADOW_CHECKS.get(event, "none"),
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries (
  delivery_id TEXT PRIMARY KEY NOT NULL UNIQUE,
  project TEXT NOT NULL, repo TEXT NOT NULL, repo_id INTEGER,
  event TEXT NOT NULL, action TEXT, sender TEXT,
  body_sha256 TEXT NOT NULL, received_at REAL NOT NULL,
  number INTEGER, comment_id INTEGER, review_id INTEGER, check_run_id INTEGER, check_suite_id INTEGER, workflow_run_id INTEGER,
  head_sha TEXT, base_sha TEXT, base_ref TEXT, head_repo TEXT, head_repo_id INTEGER,
  needs_reconcile INTEGER NOT NULL, shadow_check TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consumer_state (
  delivery_id TEXT NOT NULL REFERENCES deliveries(delivery_id), consumer TEXT NOT NULL, project TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','claimed','retrying','done','superseded','gone','dead','reconcile')),
  attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
  lease_token TEXT, lease_expires REAL, reason TEXT, updated_at REAL NOT NULL,
  PRIMARY KEY (delivery_id, consumer)
);
CREATE INDEX IF NOT EXISTS consumer_queue ON consumer_state (consumer, project, state, next_at);
CREATE TABLE IF NOT EXISTS conflicts (
  delivery_id TEXT NOT NULL, project TEXT NOT NULL, body_sha256 TEXT NOT NULL, received_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_locks (
  project TEXT PRIMARY KEY NOT NULL, holder TEXT NOT NULL, token TEXT NOT NULL, expires REAL NOT NULL
);
"""

DELIVERY_COLUMNS = ("delivery_id", "project", "repo", "repo_id", "event", "action", "sender", "body_sha256", "received_at",
                    "number", "comment_id", "review_id", "check_run_id", "check_suite_id", "workflow_run_id",
                    "head_sha", "base_sha", "base_ref", "head_repo", "head_repo_id", "needs_reconcile", "shadow_check")
INSERT_DELIVERY = ("INSERT INTO deliveries (delivery_id, project, repo, repo_id, event, action, sender, body_sha256, received_at, "
                   "number, comment_id, review_id, check_run_id, check_suite_id, workflow_run_id, "
                   "head_sha, base_sha, base_ref, head_repo, head_repo_id, needs_reconcile, shadow_check) "
                   "VALUES (:delivery_id, :project, :repo, :repo_id, :event, :action, :sender, :body_sha256, :received_at, "
                   ":number, :comment_id, :review_id, :check_run_id, :check_suite_id, :workflow_run_id, "
                   ":head_sha, :base_sha, :base_ref, :head_repo, :head_repo_id, :needs_reconcile, :shadow_check)")
SELECT_DELIVERY = ("SELECT delivery_id, project, repo, repo_id, event, action, sender, body_sha256, received_at, "
                   "number, comment_id, review_id, check_run_id, check_suite_id, workflow_run_id, "
                   "head_sha, base_sha, base_ref, head_repo, head_repo_id, needs_reconcile, shadow_check "
                   "FROM deliveries WHERE delivery_id = ?")


def default_db(home: Path) -> Path:
    return home / "events" / "events.sqlite3"


class EventStore:
    """SQLite in WAL mode, mode 600. Each method is one transaction; dedupe and leases live in the file."""

    def __init__(self, path: Path, consumers: Mapping[str, frozenset[str]] = CONSUMERS, retry: RetryPolicy | None = None) -> None:
        self.path = path
        self.consumers = consumers
        self.retry_policy = retry or RetryPolicy()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        path.chmod(0o600)
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def _commit(self) -> None:
        self.conn.execute("COMMIT")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self._commit()
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    # recording -------------------------------------------------------------------------------------

    def record(self, env: Envelope) -> Recorded:
        """Insert the delivery and its consumer rows and commit. Raises sqlite3.Error when the commit did not happen."""
        with self._transaction() as db:
            row = db.execute("SELECT body_sha256 FROM deliveries WHERE delivery_id = ?", (env.delivery_id,)).fetchone()
            if row is not None and row[0] == env.body_sha256:
                return Recorded.DUPLICATE
            if row is not None:
                db.execute("INSERT INTO conflicts VALUES (?, ?, ?, ?)", (env.delivery_id, env.project, env.body_sha256, env.received_at))
                return Recorded.CONFLICT
            values = {**dict.fromkeys(DELIVERY_COLUMNS), **env.ids, "delivery_id": env.delivery_id, "project": env.project,
                      "repo": env.repo, "repo_id": env.repo_id, "event": env.event, "action": env.action, "sender": env.sender,
                      "body_sha256": env.body_sha256, "received_at": env.received_at, "needs_reconcile": int(env.needs_reconcile),
                      "shadow_check": env.shadow_check}
            db.execute(INSERT_DELIVERY, values)
            state = State.RECONCILE if env.needs_reconcile else State.PENDING
            for consumer, events in self.consumers.items():
                if env.event in events:
                    db.execute("INSERT INTO consumer_state (delivery_id, consumer, project, state, next_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                               (env.delivery_id, consumer, env.project, state.value, env.received_at, env.received_at))
            return Recorded.INSERTED

    def delivery(self, delivery_id: str) -> Delivery | None:
        row = self.conn.execute(SELECT_DELIVERY, (delivery_id,)).fetchone()
        if row is None:
            return None
        values = dict(zip(DELIVERY_COLUMNS, row, strict=True))
        values["needs_reconcile"] = bool(values["needs_reconcile"])
        return Delivery(**values)

    def consumer_row(self, delivery_id: str, consumer: str) -> ConsumerRow | None:
        row = self.conn.execute("SELECT state, attempts, next_at, generation, reason FROM consumer_state WHERE delivery_id = ? AND consumer = ?",
                                (delivery_id, consumer)).fetchone()
        return None if row is None else ConsumerRow(State(row[0]), row[1], row[2], row[3], row[4])

    # leases -------------------------------------------------------------------------------------

    def expire_leases(self, now: float) -> int:
        """A claim whose lease ran out without a settle (a crash) goes back to pending under a new generation."""
        with self._transaction() as db:
            return self._expire(db, now)

    @staticmethod
    def _expire(db: sqlite3.Connection, now: float) -> int:
        return db.execute("UPDATE consumer_state SET state = 'pending', generation = generation + 1, lease_token = NULL, lease_expires = NULL, "
                          "updated_at = ? WHERE state = 'claimed' AND lease_expires <= ?", (now, now)).rowcount

    def claim(self, consumer: str, project: str, now: float, lease_seconds: float = 300.0) -> Lease | None:
        """The oldest due row for this consumer and project, under a fresh token and generation."""
        with self._transaction() as db:
            self._expire(db, now)
            row = db.execute("SELECT c.delivery_id, c.generation FROM consumer_state c JOIN deliveries d USING (delivery_id) "
                             "WHERE c.consumer = ? AND c.project = ? AND c.state IN ('pending', 'retrying') AND c.next_at <= ? "
                             "ORDER BY d.received_at, c.delivery_id LIMIT 1", (consumer, project, now)).fetchone()
            if row is None:
                return None
            lease = Lease(row[0], consumer, secrets.token_hex(16), row[1] + 1, now + lease_seconds)
            db.execute("UPDATE consumer_state SET state = 'claimed', generation = ?, lease_token = ?, lease_expires = ?, updated_at = ? "
                       "WHERE delivery_id = ? AND consumer = ?", (lease.generation, lease.token, lease.expires, now, lease.delivery_id, consumer))
            return lease

    def _settle(self, lease: Lease, now: float, state: State, reason: str | None, attempts: int = 0, next_at: float | None = None) -> State:
        with self._transaction() as db:
            updated = db.execute(
                "UPDATE consumer_state SET state = ?, reason = ?, attempts = attempts + ?, next_at = COALESCE(?, next_at), "
                "lease_token = NULL, lease_expires = NULL, updated_at = ? "
                "WHERE delivery_id = ? AND consumer = ? AND state = 'claimed' AND lease_token = ? AND generation = ? AND lease_expires > ?",
                (state.value, reason, attempts, next_at, now, lease.delivery_id, lease.consumer, lease.token, lease.generation, now)).rowcount
            if updated != 1:
                raise LeaseLost(f"{lease.consumer} lease on {lease.delivery_id} generation {lease.generation} is no longer current")
            return state

    def complete(self, lease: Lease, now: float, outcome: State = State.DONE, reason: str | None = None) -> State:
        """Settle a claim as done, or as a terminal non-success (superseded, gone) with its reason."""
        if outcome not in (State.DONE, State.SUPERSEDED, State.GONE):
            raise ValueError(f"complete takes done, superseded or gone, not {outcome.value}")
        return self._settle(lease, now, outcome, reason)

    def fail(self, lease: Lease, now: float, reason: str) -> State:
        """A permanent failure: the row goes to dead, which nothing deletes automatically."""
        return self._settle(lease, now, State.DEAD, reason, attempts=1)

    def retry(self, lease: Lease, now: float, reason: str, exhausted: State = State.DEAD) -> State:
        """Back off and try again later; after max_attempts the row ends in `exhausted` (dead, or gone for a confirmed not-found)."""
        row = self.consumer_row(lease.delivery_id, lease.consumer)
        attempts = (row.attempts if row else 0) + 1
        if attempts >= self.retry_policy.max_attempts:
            return self._settle(lease, now, exhausted, reason, attempts=1)
        return self._settle(lease, now, State.RETRYING, reason, attempts=1, next_at=now + self.retry_policy.delay(attempts))

    # project lock -------------------------------------------------------------------------------

    def acquire_project_lock(self, project: str, holder: str, now: float, ttl: float = 300.0) -> str | None:
        """One worker per project. A lock past its expiry belongs to a dead worker and is taken over."""
        with self._transaction() as db:
            row = db.execute("SELECT expires FROM project_locks WHERE project = ?", (project,)).fetchone()
            if row is not None and row[0] > now:
                return None
            token = secrets.token_hex(16)
            db.execute("INSERT OR REPLACE INTO project_locks VALUES (?, ?, ?, ?)", (project, holder, token, now + ttl))
            return token

    def release_project_lock(self, project: str, token: str) -> bool:
        with self._transaction() as db:
            return db.execute("DELETE FROM project_locks WHERE project = ? AND token = ?", (project, token)).rowcount == 1


def receive(raw: bytes, headers: Mapping[str, str], secret: bytes, projects: Mapping[str, str], store: EventStore, now: float) -> Response:
    """Verify, parse, bind, record, commit, and only then 202."""
    if not verify_signature(raw, _header(headers, "X-Hub-Signature-256"), secret):
        return Response(401, "missing or invalid X-Hub-Signature-256")
    env = envelope(raw, headers, projects, now)
    if isinstance(env, Response):
        return env
    try:
        recorded = store.record(env)
    except sqlite3.Error:
        return Response(503, "could not record the delivery; GitHub should redeliver", env.delivery_id)
    if recorded is Recorded.CONFLICT:
        return Response(409, "delivery id already recorded with a different body; kept the first", env.delivery_id)
    reason = "duplicate delivery; already recorded" if recorded is Recorded.DUPLICATE else "recorded"
    return Response(202, reason, env.delivery_id, env.shadow_check)


# consumers -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Live:
    """What a trusted read returned for a delivery's pointers. Fetched text never reaches here as instructions."""

    repo: str
    repo_id: int | None
    number: int | None
    head_sha: str | None


class Unavailable(Exception):
    pass


class Transient(Unavailable):
    """The read failed in a way that may pass: a timeout, a 5xx, a rate limit."""


class NotFound(Unavailable):
    """The read confirmed the object is gone: a 404 or 410."""


Fetcher = Callable[[Delivery], Live]


class Transition(Protocol):
    def gate(self, delivery: Delivery, live: Live) -> str | None:
        """A reason the transition no longer applies to the live object, or None."""

    def done(self, delivery: Delivery, live: Live) -> bool:
        """Whether the live state already shows this transition, from an earlier run that crashed before complete."""

    def apply(self, delivery: Delivery, live: Live) -> None: ...


@dataclass(frozen=True)
class Outcome:
    state: str
    delivery_id: str | None = None
    reason: str | None = None


def _mismatch(delivery: Delivery, live: Live) -> str | None:
    if live.repo.lower() != delivery.repo.lower() or (delivery.repo_id and live.repo_id and live.repo_id != delivery.repo_id):
        return f"live object is in {live.repo}, not {delivery.repo}"
    if delivery.number is not None and live.number is not None and live.number != delivery.number:
        return f"live object is #{live.number}, not #{delivery.number}"
    return None


def handle_next(store: EventStore, consumer: str, project: str, fetch: Fetcher, transition: Transition, now: float,
                holder: str, lease_seconds: float = 300.0) -> Outcome:
    """Take one row for this consumer and project under the project lock, re-fetch it, and settle it."""
    lock = store.acquire_project_lock(project, holder, now, ttl=lease_seconds)
    if lock is None:
        return Outcome("locked")
    try:
        lease = store.claim(consumer, project, now, lease_seconds)
        if lease is None:
            return Outcome("idle")
        delivery = store.delivery(lease.delivery_id)
        assert delivery is not None
        try:
            live = fetch(delivery)
        except NotFound as err:
            return Outcome(store.retry(lease, now, f"not found: {err}", exhausted=State.GONE).value, lease.delivery_id, str(err))
        except Transient as err:
            return Outcome(store.retry(lease, now, f"transient: {err}").value, lease.delivery_id, str(err))
        if (wrong := _mismatch(delivery, live)) is not None:
            return Outcome(store.fail(lease, now, wrong).value, lease.delivery_id, wrong)
        if delivery.head_sha and live.head_sha and live.head_sha != delivery.head_sha:
            moved = f"head moved from {delivery.head_sha} to {live.head_sha}"
            return Outcome(store.complete(lease, now, State.SUPERSEDED, moved).value, lease.delivery_id, moved)
        if (blocked := transition.gate(delivery, live)) is not None:
            return Outcome(store.complete(lease, now, State.SUPERSEDED, blocked).value, lease.delivery_id, blocked)
        if not transition.done(delivery, live):
            transition.apply(delivery, live)
        return Outcome(store.complete(lease, now).value, lease.delivery_id)
    finally:
        store.release_project_lock(project, lock)


# read-only views ------------------------------------------------------------------------------------


def open_read_only(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def status(conn: sqlite3.Connection | None, project: str | None = None) -> dict[str, dict[str, object]]:
    """Per project: queue depth, per-consumer states, retries, conflicts, reconciliation, and dead letters. No lease tokens."""
    if conn is None:
        return {}
    out: dict[str, dict[str, object]] = {}
    consumers: dict[str, dict[str, dict[str, int]]] = {}
    counts: dict[str, dict[str, int]] = {}

    def entry(name: str) -> dict[str, int]:
        consumers.setdefault(name, {})
        return counts.setdefault(name, {"deliveries": 0, "queue_depth": 0, "needs_reconcile": 0, "conflicts": 0, "retries": 0, "dead_letters": 0})

    for name, total, reconcile in conn.execute("SELECT project, COUNT(*), SUM(needs_reconcile) FROM deliveries "
                                               "WHERE ? IS NULL OR project = ? GROUP BY project", (project, project)):
        entry(name).update(deliveries=total, needs_reconcile=reconcile or 0)
    for name, conflicts in conn.execute("SELECT project, COUNT(*) FROM conflicts WHERE ? IS NULL OR project = ? GROUP BY project", (project, project)):
        entry(name)["conflicts"] = conflicts
    for name, depth in conn.execute("SELECT project, COUNT(DISTINCT delivery_id) FROM consumer_state "
                                    "WHERE state IN ('pending', 'retrying', 'claimed') AND (? IS NULL OR project = ?) GROUP BY project", (project, project)):
        entry(name)["queue_depth"] = depth
    for name, consumer, state, count, attempts in conn.execute(
            "SELECT project, consumer, state, COUNT(*), SUM(attempts) FROM consumer_state WHERE ? IS NULL OR project = ? "
            "GROUP BY project, consumer, state", (project, project)):
        item = entry(name)
        consumers[name].setdefault(consumer, {})[state] = count
        item["retries"] += attempts or 0
        if state == State.DEAD.value:
            item["dead_letters"] += count
    for name, item in counts.items():
        out[name] = {**item, "consumers": consumers[name]}
    return out


def dead_letters(conn: sqlite3.Connection | None, project: str | None = None) -> list[dict[str, object]]:
    if conn is None:
        return []
    rows = conn.execute("SELECT c.project, c.consumer, d.delivery_id, d.event, d.action, d.number, d.head_sha, c.attempts, c.reason, c.updated_at "
                        "FROM consumer_state c JOIN deliveries d USING (delivery_id) WHERE c.state = 'dead' AND (? IS NULL OR c.project = ?) "
                        "ORDER BY c.updated_at", (project, project))
    keys = ("project", "consumer", "delivery_id", "event", "action", "number", "head_sha", "attempts", "reason", "dead_since")
    return [dict(zip(keys, row, strict=True)) for row in rows]
