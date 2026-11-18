from __future__ import annotations

import contextlib
from pathlib import Path

from faker_kit_cards import CHIEF_KEY
from faker_kit_kanban import FOOTER, Card, Header, Kanban, KanbanError
from faker_kit_loop import CLOSED, Loop
from faker_kit_refs import number_of, numbered, prefix_of
from faker_kit_stray import stray_cards
from faker_kit_text import refresh, summary_of
from faker_kit_units import SESSION_URL, brief_source, brief_text, is_header_row, read_units, unit_index


def ledger_decisions(home: Path, chief: str) -> dict[str, str]:
    return {str(p["id"]): str(p.get("decisions") or "") for p in Loop(home, chief).read()["parts"]}


def part_plan(card: Card, header: Header, repos: dict[str, str], decisions: dict[str, str]) -> tuple[Header | None, str | None]:
    ident = str(card.key).rsplit(":", 1)[1]
    return refresh(header, card.title, repos.get(header.fields.get("Project", "")), None, None if header.fields.get("Decisions") else decisions.get(ident) or None), None


def unit_plan(card: Card, header: Header, home: Path, bot: str, repo: str, rows: list[list[str]] | None) -> tuple[Header | None, str | None]:
    unit = str(card.key).rsplit(":", 1)[1]
    found, twice = unit_index(rows)
    if unit in twice:
        return None, "the unit id is listed twice in units.tsv"
    row = found.get(unit)
    if unit == "unit" or (row is not None and is_header_row(row)) or (row is None and brief_text(home, bot, unit, None) is None):
        return None, "no units.tsv row and no brief file holds this unit"
    summary, decisions = brief_source(home, bot, unit, row)
    changes: dict[str, str | None] = {}
    if decisions and not header.fields.get("Decisions"):
        changes["Decisions"] = decisions
    repeated = header.repeated
    session = row[5].strip() if row is not None and len(row) > 5 else ""
    if SESSION_URL.match(session) and ("Session", session) not in repeated:
        repeated = (*repeated, ("Session", session))
    base = header.with_fields(changes, repeated)
    return refresh(base, card.title, repo, summary if summary != unit else summary_of(header, card.title)), None


def backfill(kanban: Kanban, home: Path, role: str, bot: str, project: str | None, repos: dict[str, str], apply: bool) -> dict:
    cards = kanban.cards(True)
    rows = read_units(home / "briefs" / bot / "orchestrate" / str(project) / "units.tsv") if role == "project" else None
    decisions = ledger_decisions(home, bot) if role == "chief" else {}
    plan: list[dict[str, str]] = []
    no_source: list[dict[str, str]] = []
    refused: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    changed = 0
    if apply:
        kanban.require_external()
    for card in cards:
        header, key = card.header, card.key or ""
        if header is None or not key.startswith("faker-kit:"):
            continue
        kind = header.fields.get("Kind", "")
        mine = (role == "chief" and kind == "part" and key.startswith(f"faker-kit:{CHIEF_KEY}:")) or (role == "project" and kind == "unit" and header.fields.get("Project") == project)
        if not mine:
            refused.append({"card": card.id, "key": key, "reason": f"a {role} bot does not rewrite {kind or 'unknown'} cards of {header.fields.get('Project', 'another project')}"})
            continue
        fresh, why = part_plan(card, header, repos, decisions) if role == "chief" else unit_plan(card, header, home, bot, repos.get(str(project), ""), rows)
        if fresh is None:
            no_source.append({"card": card.id, "key": key, "reason": str(why)})
            continue
        change = fresh != header
        item = {"card": card.id, "key": key, "layout": "old" if header.legacy else "new", "change": "rewrite" if change else "none", "text": summary_of(fresh, card.slug)}
        if change and apply:
            try:
                kanban.edit_body_if_changed(card.id, fresh, header)
                changed += 1
            except KanbanError as err:
                failed.append({"card": card.id, "reason": str(err)[:160]})
                item["change"] = "failed"
        plan.append(item)
    out: dict = {"mode": "apply" if apply else "dry-run", "cards": plan, "no_source": no_source, "refused": refused, "failed": failed,
                 "to_rewrite": sum(1 for p in plan if p["change"] != "none"), "changed": changed}
    if not apply:
        out["stray_cards"] = stray_cards(cards, role, bot, project, home)
    return out


def added_at(card: Card) -> float:
    try:
        return float(card.header.fields["Added"]) if card.header else float("inf")
    except (KeyError, ValueError):
        return float("inf")


def is_open(card: Card) -> bool:
    fields = card.header.fields if card.header else {}
    return not card.archived and card.status != "done" and fields.get("State") not in CLOSED


def assign_refs(cards: list[Card], mine: list[Card], home: Path) -> tuple[dict[str, str], dict[str, str]]:
    refs: dict[str, str] = {}
    missing: dict[str, str] = {}
    top: dict[str, int] = {}
    for card in cards:
        if card.ref and card.header:
            project = card.header.fields.get("Project", "")
            top[project] = max(top.get(project, 0), number_of(card.ref))
    for card in sorted((c for c in mine if c.header is not None and c.ref is None), key=lambda c: (added_at(c), c.id)):
        project = card.header.fields.get("Project", "") if card.header else ""
        try:
            prefix = prefix_of(project, home)
        except KanbanError as err:
            missing[card.id] = str(err)
            continue
        top[project] = top.get(project, 0) + 1
        refs[card.id] = f"{prefix}-{top[project]}"
    return refs, missing


def backfill_refs(kanban: Kanban, home: Path, role: str, bot: str, project: str | None, apply: bool) -> dict:
    if apply:
        kanban.require_external()
    with numbered(home, bot) if apply else contextlib.nullcontext():
        cards = kanban.cards(True)
        mine: list[Card] = []
        unparsed: list[dict[str, str]] = []
        for card in cards:
            header, key = card.header, card.key or ""
            if header is None:
                if FOOTER in card.body or "Key: faker-kit:" in card.body:
                    unparsed.append({"card": card.id, "reason": "the card body is not in a shape the kit can read; nothing was changed"})
                continue
            kind = header.fields.get("Kind", "")
            if (role == "chief" and kind == "part" and key.startswith(f"faker-kit:{CHIEF_KEY}:")) or (role == "project" and kind == "unit" and header.fields.get("Project") == project):
                mine.append(card)
        opened = [c for c in mine if is_open(c)]
        skipped = [{"card": c.id, "key": str(c.key), "reason": "not open"} for c in mine if not is_open(c) and c.ref is None]
        refs, missing = assign_refs(cards, opened, home)
        plan: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        changed = 0
        for card in sorted((c for c in mine if c.id in refs), key=lambda c: (added_at(c), c.id)):
            header = card.header
            assert header is not None
            item = {"card": card.id, "key": str(card.key), "ref": refs[card.id], "change": "ref"}
            if apply:
                try:
                    kanban.edit_body(card.id, header.with_fields({"Ref": refs[card.id]}, header.repeated))
                    changed += 1
                except KanbanError as err:
                    failed.append({"card": card.id, "reason": str(err)[:160]})
                    item["change"] = "failed"
            plan.append(item)
        return {"mode": "apply" if apply else "dry-run", "refs": True, "cards": plan, "no_prefix": [{"card": c, "reason": r} for c, r in missing.items()], "skipped": skipped, "unparsed": unparsed,
                "failed": failed, "to_number": len(plan), "changed": changed, "kept": sum(1 for c in opened if c.ref)}
