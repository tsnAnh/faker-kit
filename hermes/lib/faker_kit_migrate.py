from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from faker_kit_cards import UNIT_ID, completion_contract, entry_header, outcome_contract, part_key, unit_key
from faker_kit_kanban import (
    BOARD,
    Card,
    Header,
    Kanban,
    KanbanError,
    Marker,
    NewCard,
    cutover,
    marker_lock,
    read_marker,
    write_marker,
)
from faker_kit_loop import CLOSED, DEFAULT_OUTCOME, OUTCOMES, STATES, Loop, LoopError, clean_part

CLOSED_UNIT_STATES = ("merged", "abandoned", "closed", "done")
SESSION_URL = re.compile(r"\Ahttps://[^\s]{1,300}\Z")
MAX_TSV_BYTES = 2_000_000


@dataclass(frozen=True)
class Planned:
    spec: NewCard
    part: str | None = None


def unmapped_id(raw: str, fallback: str) -> str:
    text = raw.strip()
    return text[:64] if text and text.isprintable() else fallback


def plan_parts(entries: list[dict], chief: str, repos: dict[str, str]) -> tuple[list[Planned], dict[str, int], list[dict[str, str]]]:
    planned: list[Planned] = []
    closed = dict.fromkeys(CLOSED, 0)
    unmapped: list[dict[str, str]] = []
    for entry in entries:
        if entry["state"] in CLOSED:
            closed[entry["state"]] += 1
            continue
        ident = str(entry["id"])
        repo = repos.get(entry["project"])
        if repo is None:
            unmapped.append({"id": ident, "reason": f"project {entry['project']} is no longer on this machine"})
            continue
        try:
            title = clean_part(entry["part"])
        except LoopError as err:
            unmapped.append({"id": ident, "reason": str(err)[:120]})
            continue
        outcome = entry.get("outcome", DEFAULT_OUTCOME)
        if outcome not in OUTCOMES or entry["state"] not in STATES:
            unmapped.append({"id": ident, "reason": "the entry has an outcome or state faker-kit does not know"})
            continue
        shaped = {**entry, "outcome": outcome, "stages": entry.get("stages") or {}, "decisions": ""}
        header = entry_header(shaped, None, (("History", f"loop/{chief}.json#{ident}"),))
        planned.append(Planned(NewCard(header.key, title, header, chief, chief, outcome_contract(outcome, repo), completion_contract(repo))))
    return planned, closed, unmapped


def read_units(path: Path) -> list[list[str]] | None:
    try:
        if path.stat().st_size > MAX_TSV_BYTES:
            raise LoopError(f"{path.name} is too large to migrate; nothing was changed")
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise LoopError(f"{path.name} cannot be read; nothing was changed") from None
    return [line.split("\t") for line in text.splitlines() if line.strip()]


def chief_parts(home: Path) -> list[dict]:
    found: list[dict] = []
    for path in sorted((home / "loop").glob("*.json")):
        try:
            parts = json.loads(path.read_text()).get("parts")
        except (OSError, ValueError, AttributeError):
            continue
        found += [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []
    return found


def serving_part(parts: list[dict], project: str, pr: str) -> dict | None:
    matches = [p for p in parts if p.get("project") == project and p.get("state") not in CLOSED and pr.isdigit() and int(pr) in (p.get("prs") or [])]
    return min(matches, key=lambda p: int(p["id"])) if matches else None


def plan_units(rows: list[list[str]] | None, home: Path, bot: str, project: str, repo: str, now: float) -> tuple[list[Planned], int, list[dict[str, str]]]:
    planned: list[Planned] = []
    unmapped: list[dict[str, str]] = []
    closed = 0
    seen: set[str] = set()
    parts = chief_parts(home)
    for number, row in enumerate(rows or [], 1):
        ident = unmapped_id(row[0], f"row {number}")
        if len(row) < 2 or not UNIT_ID.match(row[0]):
            unmapped.append({"id": ident, "reason": "the row does not start with a unit id and a state"})
            continue
        if row[0] in seen:
            unmapped.append({"id": ident, "reason": "the unit id is listed twice"})
            continue
        seen.add(row[0])
        state = row[1].strip()
        if state in CLOSED_UNIT_STATES:
            closed += 1
            continue
        pr = row[3].strip() if len(row) > 3 else ""
        session = row[5].strip() if len(row) > 5 else ""
        serves = serving_part(parts, project, pr)
        outcome = serves.get("outcome", DEFAULT_OUTCOME) if serves else DEFAULT_OUTCOME
        card_state = state if state in ("blocked", "ready-to-merge") else "in-progress"
        fields = {"Key": unit_key(project, row[0]), "Kind": "unit", "Project": project, "Bot": bot, "Outcome": outcome, "State": card_state,
                  "Added": str(float(now)), "Updated": str(float(now))}
        if pr.isdigit():
            fields["PRs"] = pr
        if serves:
            fields["Part"] = str(serves["id"])
        links = [("History", f"briefs/{bot}/orchestrate/{project}/units.tsv#{row[0]}")]
        if pr.isdigit():
            links.append(("PR", pr))
        if SESSION_URL.match(session):
            links.append(("Session", session))
        header = Header(fields, tuple(links))
        planned.append(Planned(NewCard(header.key, row[0], header, bot, bot, outcome_contract(outcome, repo), completion_contract(repo)), part_key(int(serves["id"])) if serves else None))
    return planned, closed, unmapped


def by_key(cards: list[Card]) -> dict[str, Card]:
    found: dict[str, Card] = {}
    for card in cards:
        if card.key is not None and (card.key not in found or found[card.key].archived):
            found[card.key] = card
    return found


def report(mode: str, kanban: Kanban, **rest: object) -> dict:
    board = kanban.board()
    return {"mode": mode, "board": {"slug": board.slug, "dispatch_mode": board.dispatch_mode}, **rest}


def migrate_chief(kanban: Kanban, home: Path, chief: str, repos: dict[str, str], apply: bool, now: float) -> dict:
    entries = Loop(home, chief).read()["parts"]
    planned, closed, unmapped = plan_parts(entries, chief, repos)
    kanban.board()
    if apply:
        kanban.require_external()
    existing = by_key(kanban.cards(True))
    missing = [p for p in planned if p.spec.key not in existing]
    ids = {p.spec.key: existing[p.spec.key].id for p in planned if p.spec.key in existing}
    created: list[str] = []
    marker_written = False
    if apply:
        for item in missing:
            ids[item.spec.key] = kanban.create(item.spec).id
            created.append(item.spec.key)
        if not cutover(home):
            with marker_lock(home):
                write_marker(home, Marker(now, BOARD, sorted(ids.values()), {}))
                marker_written = True
    return report("apply" if apply else "dry-run", kanban, ledger_entries=len(entries), parts_open=len(planned), parts_closed_skipped=closed, units_open=0, units_closed=0,
                  cards_to_create=0 if apply else len(missing), cards_existing=len(planned) - len(missing), cards_created=created, links_to_make=0, links_made=0,
                  unmapped=unmapped, cutover=cutover(home), marker_written=marker_written)


def migrate_project(kanban: Kanban, home: Path, bot: str, project: str, repo: str, apply: bool, now: float) -> dict:
    rows = read_units(home / "briefs" / bot / "orchestrate" / project / "units.tsv")
    planned, closed, unmapped = plan_units(rows, home, bot, project, repo, now)
    kanban.board()
    marker = read_marker(home)
    if apply:
        kanban.require_external()
        if marker is None:
            raise KanbanError("the chief has not applied the cutover yet; units link to part cards, so the chief runs faker-kit loop migrate --apply first, and nothing was changed")
    cards = by_key(kanban.cards(True))
    missing = [p for p in planned if p.spec.key not in cards]
    parents: dict[str, list[str]] = {}
    pending: list[Planned] = []
    for item in planned:
        part = cards.get(item.part) if item.part else None
        have = cards.get(item.spec.key)
        if item.part is None or (apply and part is None):
            continue
        if part is None or have is None:
            pending.append(item)
            continue
        parents.setdefault(part.id, kanban.parents(part.id))
        if have.id not in parents[part.id]:
            pending.append(item)
    created: list[str] = []
    links_made = 0
    if apply:
        for item in missing:
            cards[item.spec.key] = kanban.create(item.spec)
            created.append(item.spec.key)
        for item in pending:
            kanban.link(cards[item.spec.key].id, cards[str(item.part)].id)
            links_made += 1
        ids = sorted(cards[p.spec.key].id for p in planned)
        with marker_lock(home):
            current = read_marker(home)
            if current is not None and current.units.get(project) != ids:
                write_marker(home, Marker(current.at, current.board, current.parts, {**current.units, project: ids}))
    unlinked = [{"id": p.spec.title, "reason": f"part card {p.part} is not on the board"} for p in planned if p.part and p.part not in cards]
    return report("apply" if apply else "dry-run", kanban, ledger_entries=0, parts_open=0, parts_closed_skipped=dict.fromkeys(CLOSED, 0), units_open=len(planned), units_closed=closed,
                  cards_to_create=0 if apply else len(missing), cards_existing=len(planned) - len(missing), cards_created=created, links_to_make=0 if apply else len(pending),
                  links_made=links_made, unmapped=unmapped, unlinked=unlinked, cutover=marker is not None)
