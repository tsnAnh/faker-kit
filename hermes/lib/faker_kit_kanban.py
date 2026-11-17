from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from faker_kit_gh import Refused, scrub

BOARD = "default"
MARKER = "kanban-cutover.json"
TIMEOUT_SECONDS = 60
EXTERNAL = "external"
MUTATING = frozenset({"create", "link", "edit", "claim", "heartbeat", "reclaim", "evidence", "complete", "archive"})
ACCEPTANCE_CLASSIFICATIONS = ("pending", "failure", "missing", "stale", "provenance", "auth", "infra", "mismatch")
EVIDENCE_KINDS = ("execution_ref", "pr_published", "pr_merged", "artifact_built", "app_installed", "other_result")
SINGLE = ("Key", "Kind", "Project", "Bot", "Outcome", "State", "Waits-on", "PRs", "Reported", "Part", "Added", "Updated", "Decisions", "Run", "Work")
REPEATED = ("Stage", "History", "PR", "Session")
HEADER_LINE = re.compile(r"\A([A-Za-z][A-Za-z-]*): (.*)\Z")
CARD_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_VALUE = 700
CLAIM_TTL = 8 * 3600
CLAIM_TTL_ENV = "HERMES_KANBAN_CLAIM_TTL_SECONDS"
MAX_RECEIPT_CHECKS = 10
MAX_CHECK_FIELD = 100
MAX_CHECK_URL = 300
CHECK_URL = re.compile(r"\Ahttps://github\.com/[A-Za-z0-9._~/%?=&#-]+\Z")
PULL_URL = re.compile(r"\Ahttps://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/pull/([0-9]{1,9})\Z")
HEAD_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
FOOTER = "---- faker-kit machine fields (read by the kit, do not edit) ----"

@dataclass(frozen=True)
class FailedCheck:
    name: str
    workflow: str
    classification: str
    url: str


@dataclass(frozen=True)
class Receipt:
    classification: str
    detail: str
    head: str
    pr: int | None
    checks: tuple[FailedCheck, ...]


def check_text(value: object, required: bool) -> str | None:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value or len(value) > MAX_CHECK_FIELD or not value.isprintable():
        return None
    return scrub(value)


def failed_check(raw: object) -> FailedCheck | None:
    if not isinstance(raw, dict) or raw.get("classification") == "success":
        return None
    name = check_text(raw.get("name"), True)
    classification = check_text(raw.get("classification"), True)
    workflow = check_text(raw.get("workflow"), False)
    url = raw.get("url")
    if name is None or classification is None or workflow is None:
        return None
    if url is not None and not (isinstance(url, str) and len(url) <= MAX_CHECK_URL and CHECK_URL.match(url)):
        return None
    return FailedCheck(name, workflow, classification, url or "")


def receipt_of(payload: dict[str, object], classification: str) -> Receipt:
    checks = payload.get("checks")
    failing = [c for c in map(failed_check, checks[:MAX_RECEIPT_CHECKS] if isinstance(checks, list) else []) if c is not None]
    head = payload.get("head_sha")
    pr_url = payload.get("pr_url")
    pull = PULL_URL.match(pr_url) if isinstance(pr_url, str) else None
    return Receipt(classification, str(payload.get("detail") or ""), head[:12] if isinstance(head, str) and HEAD_SHA.match(head) else "",
                   int(pull.group(1)) if pull else None, tuple(failing))


KanbanRunner = Callable[[list[str], str | None], str]


class KanbanError(Refused):
    pass


def cutover(home: Path) -> bool:
    return (home / MARKER).is_file()


@dataclass(frozen=True)
class Marker:
    at: float
    board: str
    parts: list[str]
    units: dict[str, list[str]]


def read_marker(home: Path) -> Marker | None:
    path = home / MARKER
    shape = KanbanError(f"the cutover marker {MARKER} is not in the expected shape; nothing was changed")
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise KanbanError(f"the cutover marker {MARKER} cannot be read; nothing was changed") from None
    if not isinstance(data, dict):
        raise shape
    parts, units, at, board = data.get("parts"), data.get("units"), data.get("at"), data.get("board")
    if not isinstance(parts, list) or not isinstance(units, dict) or not isinstance(at, (int, float)) or not isinstance(board, str):
        raise shape
    if not all(isinstance(p, str) for p in parts) or not all(isinstance(k, str) and isinstance(v, list) and all(isinstance(i, str) for i in v) for k, v in units.items()):
        raise shape
    return Marker(float(at), board, [str(p) for p in parts], {str(k): [str(i) for i in v] for k, v in units.items()})


def write_marker(home: Path, marker: Marker) -> None:
    path = home / MARKER
    tmp = path.with_name(f".{MARKER}.{os.getpid()}")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps({"at": marker.at, "board": marker.board, "parts": marker.parts, "units": marker.units}, indent=1, sort_keys=True))
    tmp.replace(path)


@contextlib.contextmanager
def marker_lock(home: Path) -> Iterator[None]:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (home / f"{MARKER}.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def check_value(name: str, value: str) -> str:
    if not value.isprintable() or len(value) > MAX_VALUE:
        raise KanbanError(f"the card field {name} must be plain text on one line of at most {MAX_VALUE} characters")
    return value


@dataclass(frozen=True)
class Header:
    fields: dict[str, str]
    repeated: tuple[tuple[str, str], ...] = ()
    text: str = ""
    legacy: bool = False

    def render(self) -> str:
        lines = [f"{name}: {check_value(name, self.fields[name])}" for name in SINGLE if name in self.fields]
        lines += [f"{name}: {check_value(name, value)}" for name, value in self.repeated]
        text = self.text.strip("\n")
        if FOOTER in text.split("\n"):
            raise KanbanError("the card text cannot hold the machine footer line")
        return "\n".join([*([text, ""] if text else []), FOOTER, *lines])

    def with_fields(self, changes: dict[str, str | None], repeated: tuple[tuple[str, str], ...] | None = None) -> Header:
        fields = {name: value for name, value in {**self.fields, **changes}.items() if value is not None}
        return Header(fields, self.repeated if repeated is None else repeated, self.text)

    def lines(self, name: str) -> list[str]:
        return [value for key, value in self.repeated if key == name]

    @property
    def key(self) -> str:
        return self.fields["Key"]


def parse_header(body: str) -> Header | None:
    whole = body.replace("\r\n", "\n")
    rows = whole.split("\n")
    legacy = FOOTER not in rows
    if legacy:
        head, _, text = whole.partition("\n\n")
    else:
        at = rows.index(FOOTER)
        text, head = "\n".join(rows[:at]).rstrip("\n"), "\n".join(rows[at + 1:])
    fields: dict[str, str] = {}
    repeated: list[tuple[str, str]] = []
    for line in head.split("\n"):
        match = HEADER_LINE.match(line)
        if match is None:
            return None
        name, value = match.groups()
        if name in REPEATED:
            repeated.append((name, value))
        elif name in SINGLE and name not in fields:
            fields[name] = value
        else:
            return None
    return Header(fields, tuple(repeated), text, legacy) if "Key" in fields else None


@dataclass(frozen=True)
class Card:
    id: str
    title: str
    status: str
    assignee: str | None
    created_by: str | None
    header: Header | None

    @property
    def archived(self) -> bool:
        return self.status == "archived"

    @property
    def key(self) -> str | None:
        return self.header.key if self.header else None


@dataclass(frozen=True)
class NewCard:
    key: str
    title: str
    header: Header
    assignee: str
    created_by: str
    outcome_contract: str
    completion_contract: str


@dataclass(frozen=True)
class Board:
    slug: str
    dispatch_mode: str


@dataclass(frozen=True)
class EvidenceRow:
    id: int
    run_id: int | None
    kind: str
    payload: dict[str, object]


def string_field(raw: dict[str, object], name: str) -> str | None:
    value = raw.get(name)
    return value if isinstance(value, str) else None


def parse_card(raw: object) -> Card:
    if not isinstance(raw, dict):
        raise KanbanError("could not read Kanban: a card is not an object; nothing was changed")
    ident, title, status = string_field(raw, "id"), string_field(raw, "title"), string_field(raw, "status")
    if ident is None or not CARD_ID.match(ident) or title is None or status is None:
        raise KanbanError("could not read Kanban: a card has no id, title, or status; nothing was changed")
    body = raw.get("body")
    return Card(ident, title, status, string_field(raw, "assignee"), string_field(raw, "created_by"), parse_header(body) if isinstance(body, str) else None)


def card_id(value: str) -> str:
    if not CARD_ID.match(value):
        raise KanbanError("a card id must be letters, digits, - and _")
    return value


def hermes_runner(bot: str) -> KanbanRunner:
    def run(args: list[str], stdin: str | None) -> str:
        try:
            env = {**os.environ, CLAIM_TTL_ENV: str(CLAIM_TTL)} if args[:1] == ["heartbeat"] else None
            done = subprocess.run(["hermes", "-p", bot, "kanban", "--board", BOARD, *args], capture_output=True, text=True, input=stdin, timeout=TIMEOUT_SECONDS, env=env)
        except FileNotFoundError:
            raise KanbanError("the hermes executable was not found") from None
        except subprocess.TimeoutExpired:
            raise KanbanError(f"no answer within {TIMEOUT_SECONDS}s") from None
        if done.returncode != 0:
            raise KanbanError(scrub(done.stderr or done.stdout) or f"exit status {done.returncode}")
        return done.stdout

    return run


class Kanban:
    def __init__(self, run: KanbanRunner) -> None:
        self.run = run
        self.known: Board | None = None

    def call(self, args: list[str], stdin: str | None = None) -> str:
        write = args[0] in MUTATING and args[:2] != ["evidence", "list"]
        try:
            return self.run(args, stdin)
        except KanbanError as err:
            if write:
                raise KanbanError(f"Kanban did not accept the change ({err}); the card was not changed") from None
            raise KanbanError(f"could not read Kanban ({err}); nothing was changed") from None

    def read_json(self, args: list[str]) -> object:
        out = self.call(args)
        try:
            return json.loads(out)
        except ValueError:
            raise KanbanError("could not read Kanban (the answer was not JSON); nothing was changed") from None

    def board(self) -> Board:
        if self.known is None:
            rows = self.read_json(["boards", "list", "--json"])
            found = next((r for r in rows if isinstance(r, dict) and r.get("slug") == BOARD), None) if isinstance(rows, list) else None
            mode = found.get("dispatch_mode") if found else None
            if not isinstance(mode, str) or mode not in ("workers", EXTERNAL):
                raise KanbanError(f"could not read Kanban (board {BOARD} has no dispatch mode); nothing was changed")
            self.known = Board(BOARD, mode)
        return self.known

    def require_external(self) -> None:
        board = self.board()
        if board.dispatch_mode != EXTERNAL:
            raise KanbanError(f"board {board.slug} is not an external board (dispatch_mode={board.dispatch_mode}); faker-kit writes cards only on an external board, so nothing was changed")

    def cards(self, archived: bool = True) -> list[Card]:
        rows = self.read_json(["list", "--json", *(["--archived"] if archived else [])])
        if not isinstance(rows, list):
            raise KanbanError("could not read Kanban (the card list is not a list); nothing was changed")
        return [parse_card(r) for r in rows]

    def find_card(self, key: str, archived: bool = False) -> Card | None:
        found = [c for c in self.cards(archived) if c.key == key]
        live = [c for c in found if not c.archived]
        if len(live) > 1:
            raise KanbanError(f"two cards hold the key {key}; nothing was changed")
        return live[0] if live else (found[0] if archived and found else None)

    def create(self, spec: NewCard) -> Card:
        self.require_external()
        args = ["create", "--body-file", "-", "--assignee", spec.assignee, "--created-by", spec.created_by, "--idempotency-key", spec.key,
                "--outcome-contract", spec.outcome_contract, "--completion-contract", spec.completion_contract, "--json", "--", spec.title]
        made = self.call(args, spec.header.render())
        try:
            card = parse_card(json.loads(made))
        except ValueError:
            raise KanbanError("Kanban did not accept the change (the answer was not JSON); the card may exist, run the command again") from None
        if card.key != spec.key:
            raise KanbanError(f"Kanban returned a card that does not hold the key {spec.key}; the card was not changed")
        return card

    def link(self, parent: str, child: str) -> None:
        self.require_external()
        self.call(["link", card_id(parent), card_id(child)])

    def edit_body(self, card: str, header: Header) -> None:
        self.require_external()
        self.call(["edit", card_id(card), "--body", header.render()])

    def edit_body_if_changed(self, card: str, header: Header, current: Header | None) -> bool:
        if current == header:
            return False
        self.edit_body(card, header)
        return True

    def claim(self, card: str, ttl: int | None = None, session: str | None = None) -> int:
        self.require_external()
        extra = [*(["--ttl", str(int(ttl))] if ttl else []), *(["--session", session] if session else [])]
        shown = self.call(["claim", card_id(card), *extra, "--json"])
        try:
            run_id = json.loads(shown).get("run_id")
        except (ValueError, AttributeError):
            run_id = None
        if not isinstance(run_id, int):
            raise KanbanError("Kanban did not accept the change (the claim has no run id); the card was not changed")
        return run_id

    def heartbeat(self, card: str, run_id: int, note: str) -> None:
        self.require_external()
        self.call(["heartbeat", card_id(card), "--run-id", str(run_id), "--note", note])

    def live_run(self, card: str) -> dict[str, object] | None:
        runs = self.shown(card).get("runs")
        if not isinstance(runs, list):
            raise KanbanError("could not read Kanban (the runs are not a list); nothing was changed")
        live = [r for r in runs if isinstance(r, dict) and isinstance(r.get("id"), int) and r.get("ended_at") is None]
        return max(live, key=lambda r: int(r["id"])) if live else None

    def release(self, card: str, reason: str) -> None:
        self.require_external()
        self.call(["reclaim", card_id(card), "--reason", reason])

    def evidence_add(self, card: str, run_id: int, kind: str, payload: dict[str, object]) -> None:
        self.require_external()
        if kind not in EVIDENCE_KINDS:
            raise KanbanError(f"evidence kind {kind} is not one Kanban takes")
        self.call(["evidence", "add", card_id(card), "--run-id", str(run_id), "--kind", kind, "--json", json.dumps(payload, sort_keys=True, separators=(",", ":"))])

    def evidence_list(self, card: str) -> list[EvidenceRow]:
        rows = self.read_json(["evidence", "list", card_id(card), "--json"])
        if not isinstance(rows, list):
            raise KanbanError("could not read Kanban (the evidence is not a list); nothing was changed")
        found: list[EvidenceRow] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int) or not isinstance(row.get("kind"), str) or not isinstance(row.get("payload"), dict):
                raise KanbanError("could not read Kanban (evidence in an unexpected shape); nothing was changed")
            run_id = row.get("run_id")
            found.append(EvidenceRow(row["id"], run_id if isinstance(run_id, int) else None, row["kind"], row["payload"]))
        return found

    def complete(self, card: str, run_id: int, pr_url: str, summary: str) -> None:
        self.require_external()
        self.call(["complete", card_id(card), "--run-id", str(run_id), "--metadata", json.dumps({"published_pr": pr_url}, sort_keys=True), "--summary", summary])

    def archive(self, card: str) -> None:
        self.require_external()
        self.call(["archive", card_id(card)])

    def shown(self, card: str) -> dict[str, object]:
        data = self.read_json(["show", card_id(card), "--json"])
        if not isinstance(data, dict):
            raise KanbanError("could not read Kanban (the card is not an object); nothing was changed")
        return data

    def parents(self, card: str) -> list[str]:
        parents = self.shown(card).get("parents")
        if not isinstance(parents, list) or not all(isinstance(p, str) for p in parents):
            raise KanbanError("could not read Kanban (the prerequisites are not a list of ids); nothing was changed")
        return [str(p) for p in parents]

    def open_run(self, card: str) -> int | None:
        runs = self.shown(card).get("runs")
        if not isinstance(runs, list):
            raise KanbanError("could not read Kanban (the runs are not a list); nothing was changed")
        open_ids = [r["id"] for r in runs if isinstance(r, dict) and isinstance(r.get("id"), int) and r.get("ended_at") is None]
        return max(open_ids) if open_ids else None

    def receipt(self, card: str, run_id: int) -> Receipt | None:
        events = self.shown(card).get("events")
        if not isinstance(events, list):
            raise KanbanError("could not read Kanban (the events are not a list); nothing was changed")
        found: Receipt | None = None
        for event in events:
            payload = event.get("payload") if isinstance(event, dict) else None
            if not isinstance(event, dict) or event.get("kind") != "pr_acceptance" or event.get("run_id") != run_id or not isinstance(payload, dict):
                continue
            classification = payload.get("classification")
            if classification in ACCEPTANCE_CLASSIFICATIONS or classification == "success":
                found = receipt_of(payload, str(classification))
        return found

    def acceptance(self, card: str, run_id: int) -> tuple[str, str] | None:
        found = self.receipt(card, run_id)
        return (found.classification, found.detail) if found else None

    def given_up_on_stale_claim(self, card: Card) -> bool:
        if card.status != "blocked":
            return False
        events = self.shown(card.id).get("events")
        if not isinstance(events, list):
            raise KanbanError("could not read Kanban (the events are not a list); nothing was changed")
        kinds = [(e.get("kind"), e.get("payload")) if isinstance(e, dict) else (None, None) for e in events]
        gave_up = max((i for i, (kind, _) in enumerate(kinds) if kind == "gave_up"), default=-1)
        unblocked = max((i for i, (kind, _) in enumerate(kinds) if kind == "unblocked"), default=-1)
        lock = f"{EXTERNAL}:{card.assignee}:"
        return gave_up > unblocked and any(kind == "reclaimed" and isinstance(payload, dict) and str(payload.get("stale_lock") or "").startswith(lock) for kind, payload in kinds[:gave_up])
