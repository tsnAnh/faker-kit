from __future__ import annotations

import contextlib
import json
import os
import re
from pathlib import Path

from faker_kit_kanban import Card, Header, Kanban, KanbanError, NewCard
from faker_kit_loop import (
    CLOSED,
    DEFAULT_OUTCOME,
    OUTCOMES,
    PAL_OUTCOME,
    STAGES,
    STATES,
    Loop,
    LoopError,
    apply_update,
    check_new,
    check_update,
    digest_view,
    held_part,
    new_entry,
    parse_evidence,
    part_row,
    show_view,
)

CHIEF_KEY = "chief"
STAGE_KIND = {"built": "artifact_built", "installed": "app_installed", "verified": "other_result"}
STAGE_FIELDS = {"built": ("name", "sha256"), "installed": ("app_id", "artifact_sha256", "host", "readback_sha256")}
VERIFIED_NAME = "faker-kit-verified"
MAX_CARD_REF = 600
UNIT_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
DIGEST_FILE = "kanban-digest.json"


def part_key(part_id: int) -> str:
    return f"faker-kit:{CHIEF_KEY}:{part_id}"


def unit_key(project: str, unit_id: str) -> str:
    return f"faker-kit:{project}:{unit_id}"


def outcome_contract(outcome: str, repo: str) -> str:
    contract: dict[str, object] = {"kind": PAL_OUTCOME[outcome]}
    if outcome == DEFAULT_OUTCOME:
        contract["repo"] = repo
    if outcome == "verified":
        contract.update({"name": VERIFIED_NAME, "required_fields": ["receipt"]})
    return json.dumps(contract, sort_keys=True, separators=(",", ":"))


def completion_contract(repo: str) -> str:
    return f"fleet-ci:{repo}"


def stage_payload(stage: str, ref: str) -> dict[str, object]:
    hint = f"with a Kanban card the {stage} evidence is a JSON object for {STAGE_KIND[stage]}"
    try:
        loaded = json.loads(ref)
    except ValueError:
        raise LoopError(hint) from None
    if not isinstance(loaded, dict):
        raise LoopError(hint)
    if stage == "verified":
        receipt = loaded.get("receipt")
        if set(loaded) - {"name", "receipt"} or loaded.get("name", VERIFIED_NAME) != VERIFIED_NAME or not isinstance(receipt, dict) or not receipt:
            raise LoopError(f'the verified evidence is {{"receipt": {{...}}}}; {hint}')
        return {"name": VERIFIED_NAME, "receipt": receipt}
    fields = STAGE_FIELDS[stage]
    if sorted(loaded) != list(fields) or not all(isinstance(loaded[f], str) and loaded[f].strip() for f in fields):
        raise LoopError(f"the {stage} evidence needs exactly these text fields: {', '.join(fields)}; {hint}")
    return {f: loaded[f] for f in fields}


def parse_card_evidence(items: list[str]) -> dict[str, dict[str, object]]:
    return {stage: stage_payload(stage, ref) for stage, ref in parse_evidence(items, MAX_CARD_REF).items()}


def stage_ref(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def number(value: str | None, card: Card, name: str) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        raise KanbanError(f"card {card.id} is not in the expected shape ({name}); nothing was changed") from None


def part_entry(card: Card) -> dict:
    header = card.header
    shape = KanbanError(f"card {card.id} is not in the expected shape; nothing was changed")
    if header is None or header.fields.get("Kind") != "part":
        raise shape
    fields = header.fields
    try:
        ident = int(header.key.rsplit(":", 1)[1])
        prs = [int(p) for p in fields.get("PRs", "").split(",") if p]
    except ValueError:
        raise shape from None
    outcome, state = fields.get("Outcome", ""), fields.get("State", "")
    if outcome not in OUTCOMES or state not in STATES or "Project" not in fields or "Bot" not in fields:
        raise shape
    stages: dict[str, str] = {}
    for line in header.lines("Stage"):
        stage, _, ref = line.partition("=")
        if stage not in STAGES:
            raise shape
        stages[stage] = ref
    return {"id": ident, "project": fields["Project"], "bot": fields["Bot"], "part": card.title, "decisions": fields.get("Decisions", ""),
            "outcome": outcome, "stages": stages, "state": state, "prs": prs, "waits_on": fields.get("Waits-on"),
            "added": number(fields.get("Added"), card, "Added"), "updated": number(fields.get("Updated"), card, "Updated"), "reported": fields.get("Reported")}


def entry_header(entry: dict, base: Header | None = None, history: tuple[tuple[str, str], ...] = ()) -> Header:
    changes: dict[str, str | None] = {
        "Key": part_key(entry["id"]), "Kind": "part", "Project": entry["project"], "Bot": entry["bot"], "Outcome": entry["outcome"], "State": entry["state"],
        "Waits-on": entry.get("waits_on"), "PRs": ",".join(str(p) for p in entry["prs"]) or None, "Reported": entry.get("reported"),
        "Added": str(float(entry["added"])), "Updated": str(float(entry["updated"])), "Decisions": entry.get("decisions") or None}
    stages = tuple(("Stage", f"{stage}={ref}") for stage, ref in sorted((entry.get("stages") or {}).items()))
    if base is None:
        return Header({k: v for k, v in changes.items() if v is not None}, (*stages, *history))
    kept = tuple((name, value) for name, value in base.repeated if name != "Stage")
    return base.with_fields({k: v for k, v in changes.items() if k != "Decisions" or v}, (*stages, *kept))


def part_cards(kanban: Kanban) -> list[Card]:
    return [c for c in kanban.cards(True) if c.header is not None and c.header.fields.get("Kind") == "part" and c.key is not None and c.key.startswith(f"faker-kit:{CHIEF_KEY}:")]


def ledger_max(home: Path, chief: str) -> int:
    parts = Loop(home, chief).read()["parts"]
    return max((int(p["id"]) for p in parts), default=0)


def read_digest_at(home: Path) -> float | None:
    try:
        value = json.loads((home / DIGEST_FILE).read_text()).get("at")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, AttributeError):
        raise LoopError(f"{DIGEST_FILE} cannot be read; nothing was changed") from None
    return float(value) if isinstance(value, (int, float)) else None


def write_digest_at(home: Path, at: float) -> None:
    path = home / DIGEST_FILE
    tmp = path.with_name(f".{DIGEST_FILE}.{os.getpid()}")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps({"at": at}))
    tmp.replace(path)


class CardLoop:
    def __init__(self, kanban: Kanban, home: Path, bot: str) -> None:
        self.kanban, self.home, self.bot = kanban, home, bot

    def locked(self) -> contextlib.AbstractContextManager[None]:
        return Loop(self.home, self.bot).locked()

    def entries(self) -> list[dict]:
        return sorted((part_entry(c) for c in part_cards(self.kanban)), key=lambda e: e["id"])

    def add(self, project: str, owner_bot: str, repo: str, text: str, decisions: str, now: float, outcome: str = DEFAULT_OUTCOME) -> dict:
        part, owner_decisions = check_new(text, decisions, outcome)
        with self.locked():
            self.kanban.require_external()
            held_part(self.entries(), project, part)
            next_id = max(max((e["id"] for e in self.entries()), default=0), ledger_max(self.home, self.bot)) + 1
            for _ in range(5):
                entry = new_entry(next_id, project, owner_bot, part, owner_decisions, outcome, now)
                made = self.kanban.create(NewCard(part_key(next_id), part, entry_header(entry), self.bot, self.bot, outcome_contract(outcome, repo), completion_contract(repo)))
                if made.title == part and made.header is not None and made.header.fields.get("Project") == project:
                    return {**part_row(entry), "card": made.id}
                next_id += 1
        raise LoopError("could not reserve a part id; run the handoff again")

    def update(self, part_id: int, state: str | None, prs: list[int], waits_on: str | None, reported: bool, now: float, evidence: list[str] | None = None) -> dict:
        check_update(state, prs, waits_on)
        payloads = parse_card_evidence(evidence or [])
        with self.locked():
            self.kanban.require_external()
            card = self.kanban.find_card(part_key(part_id), archived=True)
            if card is None or card.header is None:
                raise LoopError(f"no part {part_id}; faker-kit loop lists them")
            entry = part_entry(card)
            changed = apply_update(entry, part_id, state, prs, waits_on, reported, now, {s: stage_ref(p) for s, p in payloads.items()})
            if payloads:
                self.record(card, payloads)
            self.kanban.edit_body(card.id, entry_header(changed, card.header))
            if changed["state"] != entry["state"] and changed["state"] in ("abandoned", "taken-back"):
                self.kanban.archive(card.id)
        return part_row(changed)

    def record(self, card: Card, payloads: dict[str, dict[str, object]]) -> None:
        try:
            run_id = self.kanban.open_run(card.id)
            if run_id is None:
                run_id = self.kanban.claim(card.id)
            for stage, payload in payloads.items():
                self.kanban.evidence_add(card.id, run_id, STAGE_KIND[stage], payload)
        except KanbanError as err:
            raise LoopError(f"Kanban did not accept the evidence ({err}); the part was not changed") from None

    def show(self, everything: bool) -> dict:
        return show_view(self.entries(), everything)

    def digest(self, now: float) -> dict:
        with self.locked():
            out = digest_view(self.entries(), read_digest_at(self.home))
            write_digest_at(self.home, now)
        return out


def unit_header(project: str, bot: str, unit_id: str, outcome: str, part: int | None, prs: list[int], now: float) -> Header:
    fields = {"Key": unit_key(project, unit_id), "Kind": "unit", "Project": project, "Bot": bot, "Outcome": outcome, "State": "in-progress",
              "PRs": ",".join(str(p) for p in prs) or None, "Part": str(part) if part is not None else None, "Added": str(float(now)), "Updated": str(float(now))}
    return Header({k: v for k, v in fields.items() if v is not None})


def create_unit(kanban: Kanban, bot: str, project: str, repo: str, unit_id: str, part: int | None, prs: list[int], now: float) -> dict:
    if not UNIT_ID.match(unit_id):
        raise LoopError("a unit id is lowercase letters, digits, and hyphens, at most 64 characters")
    if any(not 0 < pr < 10**9 for pr in prs):
        raise LoopError("a pull request number must be a positive integer")
    kanban.require_external()
    outcome, parent = DEFAULT_OUTCOME, None
    if part is not None:
        parent = kanban.find_card(part_key(part), archived=True)
        if parent is None or parent.header is None or parent.header.fields.get("Project") != project:
            raise LoopError(f"no part {part} for {project}; ask the chief which part this unit serves")
        outcome = part_entry(parent)["outcome"]
    key = unit_key(project, unit_id)
    existing = kanban.find_card(key)
    header = unit_header(project, bot, unit_id, outcome, part, sorted(set(prs)), now)
    card = existing or kanban.create(NewCard(key, unit_id, header, bot, bot, outcome_contract(outcome, repo), completion_contract(repo)))
    linked = False
    if parent is not None and card.id not in kanban.parents(parent.id):
        kanban.link(card.id, parent.id)
        linked = True
    return {"unit": unit_id, "card": card.id, "key": key, "part": part, "outcome": outcome, "created": existing is None, "linked": linked}


def unit_rows(kanban: Kanban, project: str) -> dict:
    rows = []
    for card in kanban.cards(True):
        fields = card.header.fields if card.header else {}
        if fields.get("Kind") == "unit" and fields.get("Project") == project:
            rows.append({"unit": card.title, "card": card.id, "state": fields.get("State"), "prs": [int(p) for p in fields.get("PRs", "").split(",") if p.isdigit()],
                         "part": int(fields["Part"]) if fields.get("Part", "").isdigit() else None, "outcome": fields.get("Outcome"), "closed": card.archived or fields.get("State") in CLOSED})
    return {"units": sorted(rows, key=lambda r: r["unit"])}
