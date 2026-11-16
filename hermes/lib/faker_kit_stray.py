from __future__ import annotations

from pathlib import Path

from faker_kit_kanban import Card
from faker_kit_loop import CLOSED
from faker_kit_units import brief_text, is_header_row, read_units, row_closure, unit_index

CHIEF_PREFIX = "faker-kit:chief:"


def suggestion(card: Card, reason: str, kind: str) -> dict[str, str]:
    safe = reason.replace('"', "'")
    return {"card": card.id, "key": str(card.key), "reason": reason, "suggest": f'faker-kit loop close {card.id} --reason "{safe}" --as {kind}'}


def unit_findings(card: Card, home: Path, bot: str, project: str) -> tuple[str, str] | None:
    unit = str(card.key).rsplit(":", 1)[1]
    rows = read_units(home / "briefs" / bot / "orchestrate" / project / "units.tsv")
    found, twice = unit_index(rows)
    if unit in twice:
        return "the unit id is listed twice in units.tsv", "duplicate"
    row = found.get(unit)
    if row is not None and is_header_row(row):
        row = None
    if row is None and brief_text(home, bot, unit, None) is None:
        return "no units.tsv row and no brief file holds this unit", "junk"
    closure = row_closure(row[1]) if row is not None else None
    if row is not None and closure is not None:
        return f"the units.tsv row says {row[1].strip()}", "superseded" if closure == "superseded" else "folded"
    return None


def part_findings(card: Card) -> tuple[str, str] | None:
    ident = str(card.key)[len(CHIEF_PREFIX):]
    fields = card.header.fields if card.header else {}
    if not ident.isdecimal() or int(ident) < 1:
        return "the part key does not end in a part number", "junk"
    if not {"Project", "Bot", "Outcome", "State"} <= fields.keys():
        return "the part card lacks the fields a part holds", "junk"
    return None


def stray_cards(cards: list[Card], role: str, bot: str, project: str | None, home: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for card in cards:
        fields = card.header.fields if card.header else {}
        key = card.key or ""
        if card.header is None or card.archived or fields.get("State") in CLOSED:
            continue
        if role == "chief":
            verdict = part_findings(card) if fields.get("Kind") == "part" and key.startswith(CHIEF_PREFIX) else None
        else:
            own = fields.get("Kind") == "unit" and fields.get("Project") == project and key.startswith(f"faker-kit:{project}:")
            verdict = unit_findings(card, home, bot, str(project)) if own else None
        if verdict is not None:
            found.append(suggestion(card, verdict[0], verdict[1]))
    return found
