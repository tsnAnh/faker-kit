from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from faker_kit_cards import CHIEF_KEY, CLAIM_TTL, LIVE_STATES, follow_state, part_entry, unit_key
from faker_kit_kanban import Card, Kanban, KanbanError
from faker_kit_loop import CLOSED, Loop

NEAR_EXPIRY = 2 * 3600
RECONCILE = "faker-kit loop reconcile"
ALIGN = "faker-kit loop align --apply"


def visible(card: Card, role: str, project: str | None) -> bool:
    fields = card.header.fields if card.header else {}
    if card.archived or fields.get("State") in CLOSED:
        return False
    if fields.get("Kind") == "part":
        return role == "chief" and (card.key or "").startswith(f"faker-kit:{CHIEF_KEY}:")
    return fields.get("Kind") == "unit" and (role == "chief" or fields.get("Project") == project)


def as_bot(card: Card, command: str, role: str) -> str:
    return f"{command} (in {card.assignee}'s chat)" if role == "chief" and card.assignee else command


def claim_problem(kanban: Kanban, card: Card, now: float) -> str | None:
    run = kanban.live_run(card.id)
    if run is None:
        return f"card {card.status}, no run"
    expires = run.get("claim_expires")
    beat = run.get("last_heartbeat_at")
    started = run.get("started_at")
    if isinstance(expires, (int, float)) and expires - now < NEAR_EXPIRY:
        left = int((expires - now) // 60)
        return f"claim of run {run['id']} expires in {left}m" if left > 0 else f"claim of run {run['id']} expired {-left}m ago"
    if beat is None and isinstance(started, (int, float)) and now - started > CLAIM_TTL / 2:
        return f"run {run['id']} has no heartbeat since its claim"
    return None


def went_stale(kanban: Kanban, card: Card) -> int | None:
    shown = kanban.shown(card.id)
    runs, events = shown.get("runs"), shown.get("events")
    if not isinstance(runs, list) or not isinstance(events, list) or not runs:
        return None
    last = max((r for r in runs if isinstance(r, dict) and isinstance(r.get("id"), int)), key=lambda r: int(r["id"]), default=None)
    if last is None or last.get("ended_at") is None:
        return None
    stale = any(isinstance(e, dict) and e.get("kind") == "reclaimed" and e.get("run_id") == last["id"] and isinstance(e.get("payload"), dict)
                and str(e["payload"].get("stale_lock") or "").startswith("external:") for e in events)
    return int(last["id"]) if stale else None


def check(kanban: Kanban, role: str, project: str | None, open_units: dict[tuple[str, str], dict], now: float) -> dict:
    cards = kanban.cards(False)
    status = {c.id: c.status for c in cards}
    seen: set[tuple[str, str]] = set()
    problems: list[dict] = []

    def report(card: Card | None, subject: str, saw: str, fix: str) -> None:
        problems.append({"card": card.id if card else None, "subject": subject, "saw": saw, "fix": fix,
                         "line": f"{card.id if card else 'no card'} {subject}: {saw}; fix: {fix}"})

    for card in cards:
        if not visible(card, role, project) or card.header is None:
            continue
        fields = card.header.fields
        if fields["Kind"] == "part":
            state = part_entry(card)["state"]
            subject = f"part {card.key.rsplit(':', 1)[1] if card.key else card.title}"
            if state in LIVE_STATES and card.status != "running":
                units = [p for p in kanban.parents(card.id) if status.get(p) != "done"]
                if not units:
                    report(card, subject, f"ledger {state}, card {card.status}, no run", ALIGN)
                elif not any(status.get(p) == "running" for p in units):
                    report(card, subject, f"ledger {state}, card {card.status}, no run and none of its unit cards ({', '.join(units)}) running",
                           f"{RECONCILE} in {fields.get('Bot', 'the project bot')}'s chat")
                continue
            if state not in LIVE_STATES and card.status == "running":
                report(card, subject, f"ledger {state}, card running", ALIGN)
                continue
        else:
            unit = card.title
            seen.add((fields.get("Project", ""), unit))
            pull = open_units.get((fields.get("Project", ""), unit))
            live = pull is not None or bool(card.header.lines("Session"))
            subject = f"unit {unit}"
            if card.status != "running":
                lapsed = went_stale(kanban, card) if card.status == "ready" else None
                if lapsed is not None:
                    report(card, subject, f"claim of run {lapsed} went stale with no heartbeat, card {card.status}, no run", as_bot(card, RECONCILE, role))
                elif live:
                    what = f"PR #{pull['number']} {'draft ' if pull.get('draft') else ''}open" if pull else "session launched"
                    report(card, subject, f"{what}, card {card.status}, no run", as_bot(card, RECONCILE, role))
                continue
        if card.status == "running":
            problem = claim_problem(kanban, card, now)
            if problem:
                report(card, subject, problem, as_bot(card, RECONCILE, role) if fields["Kind"] == "unit" else ALIGN)
    for (proj, unit), pull in sorted(open_units.items()):
        if (proj, unit) not in seen and kanban.find_card(unit_key(proj, unit)) is None:
            report(None, f"unit {unit} in {proj}", f"PR #{pull['number']} open, no unit card", f"faker-kit loop unit {unit} --pr {pull['number']}")
    return {"problems": problems, "ok": not problems}


def fresh_claim(kanban: Kanban, card: Card, now: float) -> bool:
    run = kanban.live_run(card.id) if card.status == "running" else None
    expires = run.get("claim_expires") if run else None
    return isinstance(expires, (int, float)) and expires - now > CLAIM_TTL / 2


def hold_live_units(kanban: Kanban, project: str, bot: str, open_units: dict[tuple[str, str], dict], now: float) -> list[dict]:
    done: list[dict] = []
    for card in kanban.cards(False):
        fields = card.header.fields if card.header else {}
        if card.archived or fields.get("Kind") != "unit" or fields.get("Project") != project or card.assignee != bot or fields.get("State") in CLOSED:
            continue
        live = (project, card.title) in open_units or bool(card.header and card.header.lines("Session")) or card.status == "running"
        if not live and (card.status != "ready" or went_stale(kanban, card) is None):
            continue
        if card.status not in ("ready", "running") or fresh_claim(kanban, card, now):
            continue
        try:
            done.append({"unit": card.title, **follow_state(kanban, card, "in-progress", f"unit {card.title}")})
        except KanbanError as err:
            done.append({"unit": card.title, "card": card.id, "claim": "refused", "detail": str(err)[:160]})
    return done


def align(kanban: Kanban, home: Path, bot: str, apply: bool, follow: Callable[[Card, str], dict]) -> dict:
    plan: list[dict] = []
    with Loop(home, bot).locked():
        if apply:
            kanban.require_external()
        cards = kanban.cards(False)
        status = {c.id: c.status for c in cards}
        for card in cards:
            if not visible(card, "chief", None) or card.header is None or card.header.fields.get("Kind") != "part":
                continue
            state = part_entry(card)["state"]
            want_running = state in LIVE_STATES
            if want_running == (card.status == "running") or (want_running and any(status.get(p) != "done" for p in kanban.parents(card.id))):
                continue
            row = {"part": card.key, "card": card.id, "state": state, "status": card.status, "to": "running" if want_running else "ready"}
            if apply:
                row.update(follow(card, state))
            plan.append(row)
    return {"dry_run": not apply, "changes": plan, "count": len(plan)}
