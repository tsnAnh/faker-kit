from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from faker_kit_cards import CHIEF_KEY, completion_pending, hold, part_entry, refresh_work, unit_key
from faker_kit_gh import scrub
from faker_kit_kanban import EXTERNAL, Card, EvidenceRow, Header, Kanban, KanbanError
from faker_kit_loop import CLOSED, Loop
from faker_kit_passes import Budget, Cursor, Reach
from faker_kit_pickup import needs_brief

EXPIRY_WINDOW = 30 * 60
EXTEND_BELOW = 2 * 3600
DRAFT_AFTER_SESSION_MINUTES = 30
READY = "faker-kit loop ready"
LIVE_PARTS = ("in-progress", "ready-to-merge")
RECONCILE = "faker-kit loop reconcile"


def open_card(card: Card) -> bool:
    fields = card.header.fields if card.header else {}
    return not card.archived and card.status != "done" and fields.get("State") not in CLOSED


def chief_part(card: Card) -> bool:
    return card.header is not None and card.header.fields.get("Kind") == "part" and (card.key or "").startswith(f"faker-kit:{CHIEF_KEY}:")


def unit_card(card: Card, project: str | None) -> bool:
    fields = card.header.fields if card.header else {}
    return fields.get("Kind") == "unit" and (project is None or fields.get("Project") == project)


def session_of(header: Header) -> str | None:
    run = header.fields.get("Run", "")
    return run.split(" session ", 1)[1] if " session " in run else None


def live_unit(card: Card, open_units: dict[tuple[str, str], dict]) -> dict | None:
    fields = card.header.fields if card.header else {}
    pull = open_units.get((fields.get("Project", ""), card.slug))
    if pull is not None:
        return {"pr": pull}
    if card.header and (card.header.fields.get("Run") or card.header.lines("Session")):
        return {"session": session_of(card.header) or "launched"}
    return None


def as_bot(card: Card, command: str, role: str) -> str:
    return f"{command} (in {card.assignee}'s chat)" if role == "chief" and card.assignee else command


def last_acceptance(kanban: Kanban, card: Card) -> dict | None:
    events = kanban.shown(card.id).get("events")
    found = [e for e in events if isinstance(e, dict) and e.get("kind") == "pr_acceptance" and isinstance(e.get("payload"), dict)] if isinstance(events, list) else []
    return found[-1]["payload"] if found else None


def minutes(seconds: float) -> int:
    return int(seconds // 60)


def session_ended(kanban: Kanban, card: Card, pull: dict, summary_time: Callable[[dict], float | None]) -> float | None:
    posted = summary_time(pull)
    if posted is not None:
        return posted
    if kanban.live_run(card.id) is not None:
        return None
    runs = kanban.shown(card.id).get("runs")
    ends = [r["ended_at"] for r in runs if isinstance(r, dict) and isinstance(r.get("ended_at"), (int, float)) and not isinstance(r["ended_at"], bool)] if isinstance(runs, list) else []
    return float(max(ends)) if ends else None


def sweep(kanban: Kanban, card: Card, now: float) -> tuple[str, bool] | None:
    run = kanban.live_run(card.id)
    expires = run.get("claim_expires") if run else None
    if run is None or not isinstance(expires, (int, float)) or expires - now >= EXTEND_BELOW:
        return None
    left = minutes(expires - now)
    try:
        kanban.heartbeat(card.id, int(str(run["id"])), "faker-kit: session or pull request still live")
    except KanbanError as err:
        return f"claim of run {run['id']} expires in {left}m and could not be extended ({scrub(str(err))[:120]})", True
    after = (kanban.live_run(card.id) or {}).get("claim_expires")
    if not isinstance(after, (int, float)) or after - now < EXPIRY_WINDOW:
        return f"claim of run {run['id']} expires in {left}m; the heartbeat did not extend it past {minutes(EXPIRY_WINDOW)}m", True
    if expires - now < EXPIRY_WINDOW:
        return f"claim of run {run['id']} was {left}m from expiry; extended to {minutes(after - now)}m", False
    return None


def payload_ref(payload: dict[str, object]) -> str:
    head = payload.get("head_sha")
    return f"head {head[:12]}" if isinstance(head, str) and head else scrub(json.dumps(payload, sort_keys=True))[:80]


def duplicate_groups(rows: list[EvidenceRow]) -> list[dict]:
    groups: dict[tuple[str, str], list[EvidenceRow]] = {}
    for row in rows:
        groups.setdefault((row.kind, json.dumps(row.payload, sort_keys=True)), []).append(row)
    return [{"kind": kind, "payload": payload_ref(found[0].payload), "count": len(found), "runs": sorted({r.run_id for r in found if r.run_id is not None}), "rows": [r.id for r in found]}
            for (kind, _), found in groups.items() if len(found) > 1]


def check(kanban: Kanban, role: str, project: str | None, open_units: dict[tuple[str, str], dict], now: float, bot: str | None = None, evidence: bool = False,
          summary_time: Callable[[dict], float | None] | None = None, budget: Budget | None = None, cursor: Cursor | None = None) -> dict:
    scope = None if role == "chief" else project
    cards = kanban.cards(False)
    by_id = {c.id: c for c in cards}
    known = {(c.header.fields.get("Project", ""), c.slug) for c in cards if c.header and unit_card(c, None)}
    findings: list[dict] = []
    sweeps = role != "chief" and kanban.board().dispatch_mode == EXTERNAL

    def report(kind: str, card: Card | None, subject: str, saw: str, fix: str, action: bool = True) -> None:
        findings.append({"class": kind, "card": card.id if card else None, "subject": subject, "saw": saw, "fix": fix, "needs_action": action,
                         "line": f"{card.id if card else 'no card'} {subject}: {saw}; fix: {fix}"})

    def examine(card: Card) -> None:
        assert card.header is not None
        fields = card.header.fields
        ident = (card.key or "").rsplit(":", 1)[-1]
        subject = f"unit {card.slug}" if fields.get("Kind") == "unit" else f"part {ident}"
        if evidence:
            groups = duplicate_groups(kanban.evidence_list(card.id))
            if groups:
                shown = "; ".join(f"{g['kind']} x{g['count']} ({g['payload']}) on runs {','.join(str(r) for r in g['runs'])}" for g in groups)
                report("duplicate_evidence", card, subject, f"duplicate evidence rows: {shown}",
                       "none to run: hermes kanban evidence takes add and list only, so the kit lists the rows and never removes one; no pass adds further copies", False)
                findings[-1]["duplicates"] = groups
        if completion_pending(card):
            payload = last_acceptance(kanban, card)
            failed = payload is not None and payload.get("classification") != "success"
            said = f"acceptance {payload.get('classification')}: {scrub(str(payload.get('detail') or ''))[:120]}" if payload is not None and failed else "no acceptance receipt yet"
            fix = "none: loop reconcile retries completion on its own hold"
            if card.status == "blocked" and kanban.given_up_on_stale_claim(card):
                said, fix = f"{said}; Kanban gave the card up after two stale claims", f"hermes kanban unblock {card.id} (the owner or the chief runs it; the kit never does), then reconcile completes"
            report("merged_not_done", card, subject, f"merged and closed in the kit, Kanban has not completed it ({said})", fix, False)
            return
        if card.status == "blocked" and kanban.given_up_on_stale_claim(card):
            report("stale_blocked", card, subject, "Kanban gave the card up after two stale claims", f"hermes kanban unblock {card.id} (the owner or the chief runs it; the kit never does)")
            return
        if not open_card(card):
            return
        payload = last_acceptance(kanban, card)
        if payload is not None and payload.get("classification") != "success":
            report("merged_not_done", card, subject, f"merged pull request not done: acceptance {payload.get('classification')}: {scrub(str(payload.get('detail') or ''))[:120]}",
                   as_bot(card, RECONCILE, role))
            return
        if fields.get("Kind") == "unit":
            if role == "project" and bot and project and needs_brief(card, bot, project) and not live_unit(card, open_units):
                report("needs_brief", card, subject, "ready, but only a title and no owner decisions, so pickup never launches it", "ask the chief for a brief or owner decisions", False)
                return
            live = live_unit(card, open_units)
            draft = live.get("pr") if live else None
            if summary_time is not None and draft is not None and draft.get("draft"):
                ended = session_ended(kanban, card, draft, summary_time)
                if ended is not None and now - ended >= DRAFT_AFTER_SESSION_MINUTES * 60:
                    report("draft_after_session", card, subject, f"PR #{draft['number']} is still a draft {minutes(now - ended)}m after its session finished",
                           as_bot(card, f"{READY} (marks it when the checks passed; --dry-run says why not), or the owner reads the closing summary and marks it ready", role))
            if card.status != "running" and live:
                pull = live.get("pr")
                what = f"PR #{pull['number']} {'draft ' if pull.get('draft') else ''}open" if pull else f"session {live['session']} live"
                report("live_not_running", card, subject, f"{what}, card {card.status}, no run", as_bot(card, RECONCILE, role))
            elif card.status == "running" and sweeps:
                swept = sweep(kanban, card, now)
                if swept:
                    report("claim_expiring", card, subject, swept[0], RECONCILE, swept[1])
            return
        state = part_entry(card)["state"]
        units = [by_id[p] for p in kanban.parents(card.id) if p in by_id]
        owner_bot = fields.get("Bot", "the project bot")
        if state in LIVE_PARTS and not units:
            report("part_inconsistent", card, subject, f"ledger {state}, no unit card linked", f"faker-kit loop unit <unit-id> --part {ident} (in {owner_bot}'s chat)")
        elif state in LIVE_PARTS and not any(u.status in ("running", "done") for u in units):
            report("part_inconsistent", card, subject, f"ledger {state}, no unit card running ({', '.join(f'{u.id} {u.status}' for u in units)})",
                   f"{RECONCILE} (in {owner_bot}'s chat), or faker-kit loop set {ident} --state waiting --waits-on <project>#<n>")
        elif units and all(u.status == "done" for u in units) and card.status in ("ready", "todo"):
            report("part_inconsistent", card, subject, f"every unit card done, part card {card.status}", f"{RECONCILE}, or faker-kit loop set {ident} --state <merged|abandoned>")

    todo = [c for c in cards if c.header is not None and not c.archived and (unit_card(c, scope) or (role == "chief" and chief_part(c)))]
    if cursor is not None:
        todo = cursor.order(todo)
        cursor.keep_only(todo)
    reach = Reach(candidates=len(todo))
    for index, card in enumerate(todo):
        if budget is not None and budget.spent():
            reach.unreached = tuple(c.id for c in todo[index:])
            break
        examine(card)
        reach.reached += 1
        if cursor is not None:
            cursor.mark(card)
    archived_keys: set[str | None] | None = None
    for (proj, unit), pull in sorted(open_units.items()):
        if (proj, unit) not in known:
            archived_keys = {c.key for c in kanban.cards(True)} if archived_keys is None else archived_keys
            if unit_key(proj, unit) in archived_keys:
                continue
            report("live_not_running", None, f"unit {unit} in {proj}", f"PR #{pull['number']} open, no unit card", f"faker-kit loop unit {unit} --pr {pull['number']}")
    action = [f for f in findings if f["needs_action"]]
    return {"findings": findings, "lines": [f["line"] for f in findings], "ok": not action and not reach.partial, "needs_action": bool(action), **reach.view()}


def claimable(card: Card, project: str, bot: str, open_units: dict[tuple[str, str], dict]) -> bool:
    return unit_card(card, project) and card.assignee == bot and open_card(card) and card.status == "ready" and bool(live_unit(card, open_units))


def hold_live_units(kanban: Kanban, project: str, bot: str, open_units: dict[tuple[str, str], dict], budget: Budget | None = None) -> tuple[list[dict], list[str]]:
    done: list[dict] = []
    skipped: list[str] = []
    for card in kanban.cards(False):
        if not claimable(card, project, bot, open_units):
            continue
        if budget is not None and budget.spent():
            skipped.append(card.slug)
            continue
        label = (session_of(card.header) if card.header else None) or f"unit {card.slug} reconcile"
        try:
            done.append({"unit": card.slug, **hold(kanban, card, label)})
        except KanbanError as err:
            done.append({"unit": card.slug, "card": card.id, "claim": "refused", "detail": scrub(str(err))[:160]})
    return done, skipped


def align(kanban: Kanban, home: Path, bot: str, repos: dict[str, str], apply: bool) -> dict:
    plan: list[dict] = []
    with Loop(home, bot).locked():
        if apply:
            kanban.require_external()
        cards = kanban.cards(False)
        by_id = {c.id: c for c in cards}
        for card in cards:
            if chief_part(card) and open_card(card) and card.header is not None:
                row = refresh_work(kanban, card, by_id, repos.get(card.header.fields.get("Project", "")), apply)
                if row:
                    plan.append(row)
    return {"dry_run": not apply, "changes": plan, "count": len(plan)}
