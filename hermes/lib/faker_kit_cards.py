from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from faker_kit_gh import scrub
from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_kanban import ACCEPTANCE_CLASSIFICATIONS, CLAIM_TTL, Card, EvidenceRow, FailedCheck, Header, Kanban, KanbanError, NewCard, Receipt
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
from faker_kit_session_card import steered
from faker_kit_text import refresh
from faker_kit_units import brief_source, read_units, unit_index

CHIEF_KEY = "chief"
STAGE_KIND = {"built": "artifact_built", "installed": "app_installed", "verified": "other_result"}
STAGE_FIELDS = {"built": ("name", "sha256"), "installed": ("app_id", "artifact_sha256", "host", "readback_sha256")}
VERIFIED_NAME = "faker-kit-verified"
MAX_CARD_REF = 600
UNIT_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")
DIGEST_FILE = "kanban-digest.json"
PR_EVIDENCE = ("pr_published", "pr_merged")
CLOSE_KINDS = ("abandoned", "folded", "superseded", "duplicate", "junk")
MAX_REASON = 300
LINE_BREAKS = frozenset("\u0085\u2028\u2029")
SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
VERSION = re.compile(r"(?<![\w.])v?(\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.]+)?)(?![\w.])")
DEVICE = re.compile(r"\b(emulator-\d+)\b|\bdevice[ =:]+([A-Za-z0-9_.:-]+)")
ONE_LINE_KEYS = frozenset({"text", "head_sha", "sha256", "version", "device"})
MAX_DETAIL = 400
MAX_LINE_CHECKS = 3
NO_RECEIPT = "receipt: none for this run"
REFUSAL_CLASS = re.compile(r"PR acceptance (" + "|".join(ACCEPTANCE_CLASSIFICATIONS) + r")\b")


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


def one_line_form(stage: str) -> str:
    return f'--evidence {stage}="<what was {stage}, in one line of plain text>"'


def one_line_payload(text: str, head: str | None) -> dict[str, object]:
    facts: dict[str, object] = {"text": text}
    if head:
        facts["head_sha"] = head
    if found := SHA256.search(text):
        facts["sha256"] = found.group(0)
    if found := VERSION.search(text):
        facts["version"] = found.group(1)
    if found := DEVICE.search(text):
        facts["device"] = found.group(1) or found.group(2)
    return facts


def stage_payload(stage: str, ref: str, head: str | None = None) -> dict[str, object]:
    hint = f"write it as {one_line_form(stage)}; the command builds the {STAGE_KIND[stage]} record itself"
    if not ref.lstrip().startswith("{"):
        facts = one_line_payload(ref, head)
        return {"name": VERIFIED_NAME, "receipt": facts} if stage == "verified" else facts
    try:
        loaded = json.loads(ref)
    except ValueError:
        raise LoopError(f"the {stage} evidence is not valid JSON; {hint}") from None
    if not isinstance(loaded, dict):
        raise LoopError(hint)
    if stage == "verified":
        receipt = loaded.get("receipt")
        if set(loaded) - {"name", "receipt"} or loaded.get("name", VERIFIED_NAME) != VERIFIED_NAME or not isinstance(receipt, dict) or not receipt:
            raise LoopError(f"the verified evidence JSON is {{\"receipt\": {{...}}}}; or {hint}")
        return {"name": VERIFIED_NAME, "receipt": receipt}
    if "text" in loaded and set(loaded) <= ONE_LINE_KEYS and all(isinstance(v, str) and v.strip() for v in loaded.values()):
        return dict(loaded)
    fields = STAGE_FIELDS[stage]
    if sorted(loaded) != list(fields) or not all(isinstance(loaded[f], str) and loaded[f].strip() for f in fields):
        raise LoopError(f"the {stage} evidence JSON needs exactly these text fields: {', '.join(fields)}; or {hint}")
    return {f: loaded[f] for f in fields}


def parse_card_evidence(items: list[str], head: str | None = None) -> dict[str, dict[str, object]]:
    return {stage: stage_payload(stage, ref, head) for stage, ref in parse_evidence(items, MAX_CARD_REF).items()}


def hold(kanban: Kanban, card: Card, session: str) -> dict:
    run = kanban.live_run(card.id)
    if run is not None:
        kanban.heartbeat(card.id, int(str(run["id"])), f"faker-kit: {session} still open")
        return {"card": card.id, "claim": "heartbeat", "run_id": run["id"]}
    run_id = kanban.claim(card.id, CLAIM_TTL, session)
    return {"card": card.id, "claim": "claimed", "run_id": run_id}


def work_line(kanban: Kanban, part: Card, cards: dict[str, Card]) -> str:
    units = [cards[p] for p in kanban.parents(part.id) if p in cards]
    running = [u for u in units if u.status == "running"]
    if running:
        shown = []
        for unit in running:
            prs = unit.header.fields.get("PRs", "") if unit.header else ""
            shown.append(f"unit {unit.title} running (card {unit.id}" + (f", PR #{prs.split(',')[-1]}" if prs else "") + ")")
        return "in progress: " + "; ".join(shown)
    if units and all(u.status == "done" for u in units):
        return "every unit done"
    if units:
        return "no unit running: " + ", ".join(f"unit {u.title} {u.status} (card {u.id})" for u in units)
    return "no unit card yet"


def refresh_work(kanban: Kanban, part: Card, cards: dict[str, Card], repo: str | None, apply: bool) -> dict | None:
    if part.header is None:
        return None
    line = work_line(kanban, part, cards)
    if part.header.fields.get("Work") == line:
        return None
    if apply:
        kanban.edit_body_if_changed(part.id, refresh(part.header.with_fields({"Work": line}), part.title, repo), part.header)
    return {"part": part.key, "card": part.id, "work": line}


def let_go(kanban: Kanban, card: Card, reason: str) -> dict:
    if kanban.live_run(card.id) is None:
        return {}
    kanban.release(card.id, reason)
    return {"card": card.id, "claim": "released"}


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
    def __init__(self, kanban: Kanban, home: Path, bot: str, repos: dict[str, str] | None = None) -> None:
        self.kanban, self.home, self.bot, self.repos = kanban, home, bot, repos or {}

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
                header = refresh(entry_header(entry), part, repo)
                made = self.kanban.create(NewCard(part_key(next_id), part, header, self.bot, self.bot, outcome_contract(outcome, repo), completion_contract(repo)))
                if made.title == part and made.header is not None and made.header.fields.get("Project") == project:
                    return {**part_row(entry), "card": made.id}
                next_id += 1
        raise LoopError("could not reserve a part id; run the handoff again")

    def update(self, part_id: int, state: str | None, prs: list[int], waits_on: str | None, reported: bool, now: float, evidence: list[str] | None = None,
               head: Callable[[str, list[int]], str | None] | None = None) -> dict:
        check_update(state, prs, waits_on)
        parse_card_evidence(evidence or [])
        with self.locked():
            self.kanban.require_external()
            card = self.kanban.find_card(part_key(part_id), archived=True)
            if card is None or card.header is None:
                raise LoopError(f"no part {part_id}; faker-kit loop lists them")
            entry = part_entry(card)
            payloads = parse_card_evidence(evidence or [], head(entry["project"], sorted({*entry["prs"], *prs})) if head and evidence else None)
            changed = apply_update(entry, part_id, state, prs, waits_on, reported, now, {s: stage_ref(p) for s, p in payloads.items()})
            if payloads:
                self.record(card, payloads)
            work = work_line(self.kanban, card, {c.id: c for c in self.kanban.cards(False)})
            fresh = refresh(entry_header(changed, card.header).with_fields({"Work": work}), card.title, self.repos.get(entry["project"]))
            self.kanban.edit_body_if_changed(card.id, fresh, card.header)
            if changed["state"] != entry["state"] and changed["state"] in ("abandoned", "taken-back"):
                let_go(self.kanban, card, f"faker-kit: part {part_id} is {changed['state']}")
                self.kanban.archive(card.id)
                return part_row(changed)
        return part_row(changed)

    def record(self, card: Card, payloads: dict[str, dict[str, object]]) -> None:
        try:
            run_id = self.kanban.open_run(card.id)
            borrowed = run_id is None
            if run_id is None:
                rows = self.kanban.evidence_list(card.id)
                payloads = {stage: p for stage, p in payloads.items() if not any(r.kind == STAGE_KIND[stage] and r.payload == p for r in rows)}
                if not payloads:
                    return
                run_id = self.kanban.claim(card.id)
            try:
                for stage, payload in payloads.items():
                    self.kanban.evidence_add(card.id, run_id, STAGE_KIND[stage], payload)
            finally:
                if borrowed:
                    self.kanban.release(card.id, "faker-kit: stage evidence recorded, claim released; completion adds it again on its own run")
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


def unit_source(home: Path, bot: str, project: str, unit_id: str) -> tuple[str, str | None]:
    rows = read_units(home / "briefs" / bot / "orchestrate" / project / "units.tsv")
    found, twice = unit_index(rows)
    return brief_source(home, bot, unit_id, None if unit_id in twice else found.get(unit_id))


def create_unit(kanban: Kanban, bot: str, project: str, repo: str, unit_id: str, part: int | None, prs: list[int], now: float, home: Path) -> dict:
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
    if existing is not None:
        have = existing.header
        if have is None:
            raise LoopError(f"card {existing.id} is not in the expected shape; nothing was changed")
        bound = sorted({*(int(p) for p in have.fields.get("PRs", "").split(",") if p.isdigit()), *prs})
        changes: dict[str, str | None] = {}
        if ",".join(str(p) for p in bound) != have.fields.get("PRs", ""):
            changes = {"PRs": ",".join(str(p) for p in bound) or None, "Updated": str(float(now))}
        summary = None if have.text else unit_source(home, bot, project, unit_id)[0]
        kanban.edit_body_if_changed(existing.id, refresh(have.with_fields(changes), existing.title, repo, summary), have)
        card = existing
    else:
        summary, decisions = unit_source(home, bot, project, unit_id)
        header = unit_header(project, bot, unit_id, outcome, part, sorted(set(prs)), now)
        header = header.with_fields({"Decisions": decisions}) if decisions else header
        card = kanban.create(NewCard(key, unit_id, refresh(header, unit_id, repo, summary), bot, bot, outcome_contract(outcome, repo), completion_contract(repo)))
    linked = False
    if parent is not None and card.id not in kanban.parents(parent.id):
        kanban.link(card.id, parent.id)
        linked = True
    return {"unit": unit_id, "card": card.id, "key": key, "part": part, "outcome": outcome, "created": existing is None, "linked": linked}


def added_at(fields: dict[str, str]) -> float | None:
    try:
        return float(fields["Added"])
    except (KeyError, ValueError):
        return None


def unit_rows(kanban: Kanban, project: str) -> dict:
    rows = []
    for card in kanban.cards(True):
        fields = card.header.fields if card.header else {}
        if fields.get("Kind") == "unit" and fields.get("Project") == project:
            rows.append({"unit": card.title, "card": card.id, "state": fields.get("State"), "prs": [int(p) for p in fields.get("PRs", "").split(",") if p.isdigit()],
                         "part": int(fields["Part"]) if fields.get("Part", "").isdigit() else None, "outcome": fields.get("Outcome"), "closed": card.archived or fields.get("State") in CLOSED,
                         "added": added_at(fields),
                         "sessions": card.header.lines("Session") if card.header else [], "steered": card.header is not None and steered(card.header)})
    return {"units": sorted(rows, key=lambda r: r["unit"])}


def bound_pr_url(rows: list[EvidenceRow]) -> str | None:
    urls = sorted({str(row.payload["pr_url"]) for row in rows if row.kind in PR_EVIDENCE and isinstance(row.payload.get("pr_url"), str)})
    return urls[0] if urls else None


@dataclass(frozen=True)
class Merged:
    pr: int
    url: str
    head: str
    merge_commit: str


def completion_evidence(card: Card, outcome: str, merged: Merged) -> list[tuple[str, dict[str, object]]]:
    wanted: list[tuple[str, dict[str, object]]] = [("pr_published", {"pr_url": merged.url, "head_sha": merged.head})]
    if outcome == DEFAULT_OUTCOME:
        wanted.append(("pr_merged", {"pr_url": merged.url, "head_sha": merged.head, "merge_commit": merged.merge_commit}))
    for stage, ref in (line.partition("=")[::2] for line in (card.header.lines("Stage") if card.header else [])):
        if stage in STAGE_KIND:
            with contextlib.suppress(LoopError):
                wanted.append((STAGE_KIND[stage], stage_payload(stage, ref)))
    return wanted


def release_claim(kanban: Kanban, card_id: str) -> dict:
    try:
        kanban.release(card_id, "faker-kit: completion refused, claim released so the next pass starts clean")
    except KanbanError as err:
        return {"claim_left_open": scrub(str(err))[:120]}
    return {}


def complete_card(kanban: Kanban, card: Card, outcome: str, merged: Merged) -> dict:
    run_id: int | None = None
    try:
        run_id = kanban.open_run(card.id)
        if run_id is None:
            run_id = kanban.claim(card.id)
        for kind, payload in completion_evidence(card, outcome, merged):
            kanban.evidence_add(card.id, run_id, kind, payload)
        kanban.complete(card.id, run_id, merged.url, f"Merged pull request #{merged.pr}")
    except KanbanError as err:
        refused = {"id": card.id, "completion": "refused", **refusal_detail(kanban, card.id, run_id, str(err))}
        return {**refused, **release_claim(kanban, card.id)} if run_id is not None else refused
    return {"id": card.id, "completion": "done"}


def check_line(check: FailedCheck) -> str:
    where = f" ({check.workflow})" if check.workflow else ""
    link = f": {check.url}" if check.url else ""
    return scrub(f"CI {check.classification} on {check.name}{where}{link}")


def receipt_line(receipt: Receipt) -> str:
    shown = "; ".join(check_line(c) for c in receipt.checks[:MAX_LINE_CHECKS])
    more = f"; +{len(receipt.checks) - MAX_LINE_CHECKS} more" if len(receipt.checks) > MAX_LINE_CHECKS else ""
    pr = f", pull request #{receipt.pr}" if receipt.pr is not None else ""
    head = f"head {receipt.head}{pr}: " if receipt.head else (f"pull request #{receipt.pr}: " if receipt.pr is not None else "")
    return f"{head}{shown}{more}"


def refusal_detail(kanban: Kanban, card_id: str, run_id: int | None, text: str) -> dict:
    receipt = None
    if run_id is not None:
        try:
            receipt = kanban.receipt(card_id, run_id)
        except KanbanError:
            receipt = None
    if receipt is None:
        named = REFUSAL_CLASS.search(text)
        return {"classification": named.group(1) if named else "unknown", "detail": f"{scrub(text)} ({NO_RECEIPT})"}
    if receipt.checks:
        fields = [{"name": c.name, "workflow": c.workflow, "classification": c.classification, "url": c.url} for c in receipt.checks]
        return {"classification": receipt.classification, "checks": fields, "detail": receipt_line(receipt)[:MAX_DETAIL]}
    return {"classification": receipt.classification, "detail": scrub(receipt.detail or text)[:MAX_DETAIL]}


def clean_reason(text: str | None) -> str:
    raw = (text or "").strip()
    if not raw or not raw.isprintable() or any(ch in LINE_BREAKS for ch in raw) or len(raw) > MAX_REASON:
        raise LoopError(f"say why in plain text on one line of at most {MAX_REASON} characters: --reason <why>")
    if SECRET_SHAPES.search(raw):
        raise LoopError("the reason looks like it holds a token or key; say why in plain words")
    return raw


def close_target(kanban: Kanban, role: str, project: str | None, target: int | str) -> Card:
    text = str(target)
    cards = kanban.cards(True)
    if text.startswith("faker-kit:") or text.startswith("t_"):
        found = [c for c in cards if (c.key == text if text.startswith("faker-kit:") else c.id == text)]
    elif role == "chief":
        found = [c for c in cards if c.key == part_key(int(text))] if text.isdecimal() else []
    else:
        found = [c for c in cards if c.key == unit_key(str(project), text)]
    live = [c for c in found if not c.archived]
    if len(live) > 1:
        raise LoopError(f"two cards hold {text}; nothing was changed")
    card = live[0] if live else (found[0] if found else None)
    if card is None:
        raise LoopError(f"no card for {text}; nothing was changed")
    return card


def close_card(kanban: Kanban, home: Path, role: str, bot: str, project: str | None, repos: dict[str, str], target: int | str, reason: str | None, kind: str, now: float, dry_run: bool) -> dict:
    why = clean_reason(reason)
    if kind not in CLOSE_KINDS:
        raise LoopError(f"--as must be one of {', '.join(CLOSE_KINDS)}")
    with Loop(home, bot).locked():
        if not dry_run:
            kanban.require_external()
        card = close_target(kanban, role, project, target)
        header = card.header
        fields = header.fields if header else {}
        named = isinstance(target, str) and (target.startswith("faker-kit:") or target.startswith("t_"))
        if role == "chief":
            allowed = (fields.get("Kind") == "part" and (card.key or "").startswith(f"faker-kit:{CHIEF_KEY}:")) or (named and fields.get("Kind") in ("part", "unit"))
        else:
            allowed = fields.get("Kind") == "unit" and fields.get("Project") == project
        if header is None or not allowed:
            raise LoopError(f"card {card.id} is not one {bot} may close; nothing was changed")
        if any(SECRET_SHAPES.search(v) for v in (*fields.values(), *(v for _, v in header.repeated))):
            raise LoopError(f"card {card.id} holds text that looks like a token or key; nothing was changed")
        if card.archived or fields.get("State") in CLOSED:
            raise LoopError(f"card {card.id} is already closed; nothing was changed")
        if (fields.get("PRs") or header.lines("PR")) and kind not in ("superseded", "folded"):
            raise LoopError(f"card {card.id} records a pull request; close it with --as superseded or --as folded, or let loop reconcile settle it; nothing was changed")
        changed = header.with_fields({"State": "abandoned", "Waits-on": None, "Updated": str(float(now))}, (*header.repeated, ("History", f"closed {kind}: {why}")))
        repo = repos.get(fields.get("Project", ""))
        fresh = refresh(changed, card.title, repo)
        out = {"card": card.id, "key": card.key, "kind": kind, "reason": why}
        if dry_run:
            return {**out, "closed": False, "dry_run": True, "would_record": {"state": "abandoned", "text": fresh.text.split("\n", 1)[0], "closed": f"Closed ({kind}): {why}"}}
        fresh.render()
        kanban.edit_body_if_changed(card.id, fresh, header)
        let_go(kanban, card, f"faker-kit: closed {kind}")
        kanban.archive(card.id)
        return {**out, "closed": True, "archived": True}
