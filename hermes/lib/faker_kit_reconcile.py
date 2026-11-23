from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from faker_kit_cards import CardLoop, Merged, bound_pr_url, complete_card, completion_pending, let_go, park_card, part_entry, part_key, waiting_reason
from faker_kit_gh import Refused, scrub
from faker_kit_kanban import Card, Kanban, KanbanError
from faker_kit_loop import CLOSED, DEFAULT_OUTCOME, Loop
from faker_kit_refusals import Refusals
from faker_kit_text import refresh

PullReader = Callable[[str, int], dict]
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
CLOSED_UNIT_STATES = ("merged", "abandoned", "closed", "done")
ABANDONED_STATES = ("abandoned", "taken-back")


def recorded_prs(text: str) -> list[int]:
    return [int(p) for p in text.split(",") if p.isdigit()]


class Unreadable(Exception):
    pass


@dataclass(frozen=True)
class Tracked:
    ident: str
    kind: str
    project: str
    repo: str | None
    prs: tuple[int, ...]
    outcome: str
    stages: dict[str, str]
    card: Card | None = None
    problem: str | None = None


@dataclass(frozen=True)
class Seen:
    number: int
    open: bool
    merged: bool
    url: str
    head: str
    merge_commit: str
    merged_at: str
    draft: bool = False


@dataclass
class Findings:
    read: int = 0
    merged: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    unreadable: list[dict[str, str]] = field(default_factory=list)
    awaiting: list[dict[str, str]] = field(default_factory=list)
    waiting: list[dict[str, object]] = field(default_factory=list)
    refused: list[dict[str, object]] = field(default_factory=list)
    pending: list[dict[str, object]] = field(default_factory=list)
    stale_blocked: list[dict[str, object]] = field(default_factory=list)


class Settler:
    def merge(self, item: Tracked, last: Seen) -> tuple[str, dict[str, object]]:
        raise NotImplementedError

    def close(self, item: Tracked) -> None:
        raise NotImplementedError

    def publish(self, item: Tracked, live: Seen) -> bool:
        raise NotImplementedError


def seen_pull(raw: dict, repo: str, number: int) -> Seen:
    state, merged_at = raw.get("state"), raw.get("merged_at")
    if raw.get("number") != number or str(raw.get("html_url") or "").lower() != f"https://github.com/{repo}/pull/{number}".lower():
        raise Unreadable(f"GitHub's answer for #{number} is not a pull request of {repo}")
    if state not in ("open", "closed") or "merged_at" not in raw or (merged_at is not None and not isinstance(merged_at, str)) or (merged_at and state != "closed"):
        raise Unreadable(f"GitHub's answer for #{number} has no usable state or merged_at")
    head = str((raw.get("head") or {}).get("sha") or "")
    return Seen(number, state == "open", bool(merged_at), str(raw["html_url"]), head, str(raw.get("merge_commit_sha") or ""), str(merged_at or ""), raw.get("draft") is True)


def read_seen(read: PullReader, repo: str, number: int) -> Seen:
    try:
        raw = read(repo, number)
    except (Refused, ValueError) as err:
        raise Unreadable(scrub(str(err)) or f"#{number} could not be read") from None
    return seen_pull(raw, repo, number)


def reconcile(items: list[Tracked], read: PullReader, settle: Settler | None) -> Findings:
    found = Findings()
    cache: dict[tuple[str, int], Seen | Unreadable] = {}
    for item in items:
        if item.problem or item.repo is None:
            found.unreadable.append({"id": item.ident, "reason": item.problem or f"project {item.project} is no longer on this machine"})
            continue
        seen: list[Seen] = []
        reason = ""
        for number in item.prs:
            key = (item.repo, number)
            if key not in cache:
                try:
                    cache[key] = read_seen(read, item.repo, number)
                except Unreadable as err:
                    cache[key] = err
            got = cache[key]
            if isinstance(got, Unreadable):
                reason = str(got)
                break
            seen.append(got)
        if reason:
            found.unreadable.append({"id": item.ident, "reason": reason})
        elif any(s.open for s in seen):
            found.unchanged.append(item.ident)
            live = max((s for s in seen if s.open and not s.draft), key=lambda s: s.number, default=None)
            if settle is not None and live is not None and item.kind == "unit":
                publish(found, item, live, settle)
        elif any(s.merged for s in seen):
            land(found, item, max((s for s in seen if s.merged), key=lambda s: s.merged_at), settle)
        elif settle is not None:
            settle.close(item)
            found.closed.append(item.ident)
        else:
            found.closed.append(item.ident)
    found.read = len(cache)
    return found


def publish(found: Findings, item: Tracked, live: Seen, settle: Settler) -> None:
    try:
        if settle.publish(item, live):
            found.published.append(item.ident)
    except KanbanError as err:
        found.refused.append({"id": item.ident, "card": item.card.id if item.card else item.ident, "head": live.head, "completion": "evidence_refused",
                              "classification": "unknown", "detail": scrub(str(err))[:160]})


def land(found: Findings, item: Tracked, last: Seen, settle: Settler | None) -> None:
    if item.outcome != DEFAULT_OUTCOME and item.outcome not in item.stages:
        if isinstance(settle, CardSettler) and item.kind == "unit":
            settle.landed(item, last)
        found.awaiting.append({"id": item.ident, "outcome": item.outcome})
        return
    if settle is None:
        found.merged.append(item.ident)
        return
    status, detail = settle.merge(item, last)
    if status == "done":
        found.merged.append(item.ident)
    elif status == "waiting":
        found.waiting.append({"id": item.ident, **detail})
    elif status == "stale_blocked":
        found.stale_blocked.append({"id": item.ident, **detail})
    elif status == "pending":
        found.pending.append({"id": item.ident, **detail})
    else:
        found.refused.append({"id": item.ident, **detail})


def entries(found: Findings) -> dict[str, object]:
    return {"unchanged": found.unchanged, "published": found.published, "unreadable": found.unreadable, "awaiting_stage_evidence": found.awaiting, "waiting_on_units": found.waiting,
            "refused": found.refused, "completion_pending": found.pending, "stale_blocked": found.stale_blocked}


def render(found: Findings, mode: str) -> dict:
    rest = {"unchanged": len(found.unchanged), "unreadable": len(found.unreadable), "refused": len(found.refused),
            "completion_pending": len(found.pending), "stale_blocked": len(found.stale_blocked)}
    if mode == "dry-run":
        return {"read": found.read, "would_set_merged": len(found.merged), "would_set_closed": len(found.closed), **rest,
                "entries": {"merged": found.merged, "closed": found.closed, **entries(found)}}
    if mode == "report":
        return {"mode": mode, "read": found.read, "set_merged": 0, "set_closed": 0, **rest, "would_set": {"merged": found.merged, "closed": found.closed},
                "entries": {"merged": [], "closed": [], **entries(found)}}
    return {"mode": mode, "read": found.read, "set_merged": len(found.merged), "set_closed": len(found.closed), **rest,
            "entries": {"merged": found.merged, "closed": found.closed, **entries(found)}}


def counts(rendered: dict) -> dict[str, object]:
    return {k: rendered[k] for k in ("mode", "read", "set_merged", "set_closed", "unchanged", "unreadable", "refused",
        "completion_pending", "stale_blocked")} | {"unreadable_ids": [u["id"] for u in rendered["entries"]["unreadable"]]}


def ledger_items(parts: list[dict], repos: dict[str, str]) -> list[Tracked]:
    return [Tracked(str(p["id"]), "part", p["project"], repos.get(p["project"]), tuple(int(n) for n in p["prs"]), p.get("outcome", DEFAULT_OUTCOME), dict(p.get("stages") or {}))
            for p in parts if p["state"] not in CLOSED and p["prs"]]


def tsv_items(rows: list[list[str]] | None, parts: list[dict], project: str, repo: str) -> list[Tracked]:
    items: list[Tracked] = []
    seen: set[str] = set()
    for row in rows or []:
        pr = row[3].strip() if len(row) > 3 else ""
        if len(row) < 2 or row[0] in seen or row[1].strip() in CLOSED_UNIT_STATES or not pr.isdigit() or int(pr) < 1:
            continue
        seen.add(row[0])
        serving = [p for p in parts if p.get("project") == project and p.get("state") not in CLOSED and int(pr) in (p.get("prs") or [])]
        outcome = min(serving, key=lambda p: int(p["id"])).get("outcome", DEFAULT_OUTCOME) if serving else DEFAULT_OUTCOME
        stages = dict(min(serving, key=lambda p: int(p["id"])).get("stages") or {}) if serving else {}
        items.append(Tracked(row[0], "unit", project, repo, (int(pr),), outcome, stages))
    return items


def card_prs(kanban: Kanban, card: Card, repo: str | None) -> tuple[tuple[int, ...], str | None]:
    fields = card.header.fields if card.header else {}
    bound, problem = evidence_numbers(kanban, card, repo)
    header_prs = [int(p) for p in fields.get("PRs", "").split(",") if p.isdigit()]
    return tuple(sorted(set(bound or header_prs))), problem


def evidence_numbers(kanban: Kanban, card: Card, repo: str | None) -> tuple[list[int], str | None]:
    rows = kanban.evidence_list(card.id)
    bound = bound_pr_url(rows)
    if bound is None:
        return [], None
    found = re.fullmatch(rf"https://github\.com/{re.escape(repo or '')}/pull/([1-9][0-9]{{0,8}})", bound, re.I)
    return ([int(found.group(1))] if found else []), (None if found else f"card {card.id} is bound to a pull request outside {repo}")


def serving_stages(by_key: dict[str, Card], fields: dict[str, str]) -> dict[str, str]:
    number = fields.get("Part", "")
    part = by_key.get(part_key(int(number))) if number.isdigit() else None
    if part is None or part.header is None:
        return {}
    try:
        return dict(part_entry(part)["stages"])
    except KanbanError:
        return {}


def card_items(kanban: Kanban, cards: list[Card], kind: str, repos: dict[str, str], project: str | None = None, owner: str | None = None) -> list[Tracked]:
    items: list[Tracked] = []
    by_key = {c.key: c for c in cards if c.key}
    for card in cards:
        fields = card.header.fields if card.header else {}
        closed = fields.get("State") in CLOSED and not completion_pending(card)
        if card.archived or fields.get("Kind") != kind or closed or (project is not None and fields.get("Project") != project) or (owner is not None and card.assignee != owner):
            continue
        repo = repos.get(fields.get("Project", ""))
        if kind == "part":
            entry = part_entry(card)
            ident, outcome, stages = str(entry["id"]), entry["outcome"], dict(entry["stages"])
            bound, problem = evidence_numbers(kanban, card, repo)
            prs = tuple(sorted(set(bound or entry["prs"])))
        else:
            ident, outcome = card.slug, fields.get("Outcome", DEFAULT_OUTCOME)
            stages = {**serving_stages(by_key, fields), **dict(line.partition("=")[::2] for line in card.header.lines("Stage"))} if card.header else {}
            prs, problem = card_prs(kanban, card, repo)
        if prs or problem:
            items.append(Tracked(ident, kind, fields.get("Project", ""), repo, prs, outcome, stages, card, problem))
    return items


class CardSettler(Settler):
    def __init__(self, kanban: Kanban, loop: CardLoop, cards: list[Card], now: float) -> None:
        self.kanban, self.loop, self.now = kanban, loop, now
        self.refusals = Refusals(loop.home, loop.bot)
        self.cards = {c.id: c for c in cards}

    def open_units(self, card: Card) -> list[str]:
        return [p for p in self.kanban.parents(card.id) if self.unit_open(self.cards.get(p))]

    @staticmethod
    def unit_open(unit: Card | None) -> bool:
        if unit is None:
            return True
        return unit.status not in ("done", "archived") and (unit.header is None or unit.header.fields.get("State") not in ABANDONED_STATES)

    def merge(self, item: Tracked, last: Seen) -> tuple[str, dict[str, object]]:
        card = item.card
        if card is None:
            return "refused", {"completion": "refused", "classification": "unknown", "detail": "the entry has no card"}
        if card.status == "done":
            self.mark(item, card, "merged", last.number, None)
            return "done", {}
        open_units = self.open_units(card) if item.kind == "part" else []
        if open_units:
            return "waiting", {"card": card.id, "units": open_units}
        if self.kanban.given_up_on_stale_claim(card):
            self.mark(item, card, "merged", last.number)
            return "stale_blocked", {"card": card.id, "completion": "stale_blocked", "unblock": f"hermes kanban unblock {card.id}",
                                     "detail": "Kanban gave this card up after a stale kit claim; the owner or chief can run the unblock line, then loop reconcile completes it"}
        if not SHA.match(last.merge_commit) or not SHA.match(last.head):
            said = f"GitHub reports no merge commit for #{last.number} yet; loop reconcile completes the card once it does"
            self.mark(item, card, "merged", last.number, said)
            return "pending", {"card": card.id, "head": last.head, "completion": "pending", "classification": "unknown", "detail": said, **self.park(card, said)}
        merged = Merged(last.number, last.url, last.head, last.merge_commit)
        result = self.refusals.attempt(card.id, last.number, last.head, self.now, lambda: complete_card(self.kanban, card, item.outcome, merged, item.stages))
        done = result["completion"] == "done"
        waiting = None if done else waiting_reason(result)
        self.mark(item, card, "merged", last.number, waiting)
        if done:
            return "done", {}
        parked = self.park(card, waiting or "") if result.get("held") else {}
        return "pending", {"card": result.get("id", card.id), "head": last.head, **{k: v for k, v in result.items() if k != "id"}, **parked, "completion": "pending"}

    def park(self, card: Card, reason: str) -> dict:
        return park_card(self.kanban, card.id, reason, False) if card.status == "ready" else {}

    def landed(self, item: Tracked, last: Seen) -> None:
        card = item.card
        if card is None or card.status == "done":
            return
        said = f"the {item.outcome} evidence is not recorded on the card yet"
        self.mark(item, card, "merged", last.number, said)
        if card.status == "ready":
            park_card(self.kanban, card.id, said, False)
        elif card.status == "running" and self.kanban.live_run(card.id) is not None:
            park_card(self.kanban, card.id, said, True)

    def close(self, item: Tracked) -> None:
        if item.card is not None:
            let_go(self.kanban, item.card, "faker-kit: the pull request closed unmerged, claim released")
            self.mark(item, item.card, "abandoned", None)

    def publish(self, item: Tracked, live: Seen) -> bool:
        card = item.card
        if card is None or card.status == "done" or item.outcome != DEFAULT_OUTCOME or not SHA.match(live.head):
            return False
        run_id = self.kanban.open_run(card.id)
        if run_id is None:
            return False
        payload: dict[str, object] = {"pr_url": live.url, "head_sha": live.head}
        return self.kanban.evidence_add(card.id, run_id, "pr_published", payload)

    def mark(self, item: Tracked, card: Card, state: str, pr: int | None, waiting: str | None = None) -> None:
        extra = {"Acceptance": waiting} if state == "merged" else None
        if item.kind == "part":
            if not already_merged(card, state, pr) or acceptance_moves(card, extra):
                self.loop.update(int(item.ident), state, [pr] if pr else [], None, False, self.now, extra=extra)
            return
        mark_unit(self.kanban, card, state, pr, self.now, item.repo, extra)


def already_merged(card: Card, state: str, pr: int | None) -> bool:
    header = card.header
    return state == "merged" and header is not None and header.fields.get("State") == "merged" and pr is not None and pr in recorded_prs(header.fields.get("PRs", ""))


def acceptance_moves(card: Card, extra: dict[str, str | None] | None) -> bool:
    return extra is not None and card.header is not None and card.header.fields.get("Acceptance") != extra["Acceptance"]


def mark_unit(kanban: Kanban, card: Card, state: str, pr: int | None, now: float, repo: str | None, extra: dict[str, str | None] | None = None) -> None:
    header = card.header
    moved = not already_merged(card, state, pr)
    if header is not None and (moved or acceptance_moves(card, extra)):
        prs = sorted({*recorded_prs(header.fields.get("PRs", "")), *([pr] if pr else [])})
        changes: dict[str, str | None] = {"State": state, "PRs": ",".join(str(p) for p in prs) or None, **(extra or {})}
        if moved:
            changes["Updated"] = str(float(now))
        kanban.edit_body_if_changed(card.id, refresh(header.with_fields(changes), card.title, repo), header)
    if state == "abandoned":
        kanban.archive(card.id)


class LoopSettler(Settler):
    def __init__(self, home: Path, bot: str, now: float) -> None:
        self.loop, self.now = Loop(home, bot), now

    def merge(self, item: Tracked, last: Seen) -> tuple[str, dict[str, object]]:
        self.loop.update(int(item.ident), "merged", [last.number], None, False, self.now)
        return "done", {}

    def close(self, item: Tracked) -> None:
        self.loop.update(int(item.ident), "abandoned", [], None, False, self.now)
