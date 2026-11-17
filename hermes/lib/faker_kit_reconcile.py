from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from faker_kit_cards import CardLoop, Merged, bound_pr_url, complete_card, let_go, part_entry
from faker_kit_gh import Refused, scrub
from faker_kit_kanban import Card, Kanban
from faker_kit_loop import CLOSED, DEFAULT_OUTCOME, Loop
from faker_kit_text import refresh

PullReader = Callable[[str, int], dict]
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
CLOSED_UNIT_STATES = ("merged", "abandoned", "closed", "done")


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
    stages: frozenset[str]
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


@dataclass
class Findings:
    read: int = 0
    merged: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    unreadable: list[dict[str, str]] = field(default_factory=list)
    awaiting: list[dict[str, str]] = field(default_factory=list)
    waiting: list[dict[str, object]] = field(default_factory=list)
    refused: list[dict[str, object]] = field(default_factory=list)
    stale_blocked: list[dict[str, object]] = field(default_factory=list)


class Settler:
    def merge(self, item: Tracked, last: Seen) -> tuple[str, dict[str, object]]:
        raise NotImplementedError

    def close(self, item: Tracked) -> None:
        raise NotImplementedError


def seen_pull(raw: dict, repo: str, number: int) -> Seen:
    state, merged_at = raw.get("state"), raw.get("merged_at")
    if raw.get("number") != number or str(raw.get("html_url") or "").lower() != f"https://github.com/{repo}/pull/{number}".lower():
        raise Unreadable(f"GitHub's answer for #{number} is not a pull request of {repo}")
    if state not in ("open", "closed") or "merged_at" not in raw or (merged_at is not None and not isinstance(merged_at, str)) or (merged_at and state != "closed"):
        raise Unreadable(f"GitHub's answer for #{number} has no usable state or merged_at")
    head = str((raw.get("head") or {}).get("sha") or "")
    return Seen(number, state == "open", bool(merged_at), str(raw["html_url"]), head, str(raw.get("merge_commit_sha") or ""), str(merged_at or ""))


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
        elif any(s.merged for s in seen):
            land(found, item, max((s for s in seen if s.merged), key=lambda s: s.merged_at), settle)
        elif settle is not None:
            settle.close(item)
            found.closed.append(item.ident)
        else:
            found.closed.append(item.ident)
    found.read = len(cache)
    return found


def land(found: Findings, item: Tracked, last: Seen, settle: Settler | None) -> None:
    if item.outcome != DEFAULT_OUTCOME and item.outcome not in item.stages:
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
    else:
        found.refused.append({"id": item.ident, **detail})


def entries(found: Findings) -> dict[str, object]:
    return {"unchanged": found.unchanged, "unreadable": found.unreadable, "awaiting_stage_evidence": found.awaiting, "waiting_on_units": found.waiting,
            "refused": found.refused, "stale_blocked": found.stale_blocked}


def render(found: Findings, mode: str) -> dict:
    rest = {"unchanged": len(found.unchanged), "unreadable": len(found.unreadable), "refused": len(found.refused),
            "stale_blocked": len(found.stale_blocked)}
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
        "stale_blocked")} | {"unreadable_ids": [u["id"] for u in rendered["entries"]["unreadable"]]}


def ledger_items(parts: list[dict], repos: dict[str, str]) -> list[Tracked]:
    return [Tracked(str(p["id"]), "part", p["project"], repos.get(p["project"]), tuple(int(n) for n in p["prs"]), p.get("outcome", DEFAULT_OUTCOME), frozenset((p.get("stages") or {}).keys()))
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
        stages = frozenset((min(serving, key=lambda p: int(p["id"])).get("stages") or {}).keys()) if serving else frozenset()
        items.append(Tracked(row[0], "unit", project, repo, (int(pr),), outcome, stages))
    return items


def evidence_numbers(kanban: Kanban, card: Card, repo: str | None) -> tuple[list[int], str | None]:
    rows = kanban.evidence_list(card.id)
    bound = bound_pr_url(rows)
    if bound is None:
        return [], None
    found = re.fullmatch(rf"https://github\.com/{re.escape(repo or '')}/pull/([1-9][0-9]{{0,8}})", bound, re.I)
    return ([int(found.group(1))] if found else []), (None if found else f"card {card.id} is bound to a pull request outside {repo}")


def card_items(kanban: Kanban, cards: list[Card], kind: str, repos: dict[str, str], project: str | None = None, owner: str | None = None) -> list[Tracked]:
    items: list[Tracked] = []
    for card in cards:
        fields = card.header.fields if card.header else {}
        if card.archived or fields.get("Kind") != kind or fields.get("State") in CLOSED or (project is not None and fields.get("Project") != project) or (owner is not None and card.assignee != owner):
            continue
        repo = repos.get(fields.get("Project", ""))
        if kind == "part":
            entry = part_entry(card)
            header_prs, ident, outcome, stages = entry["prs"], str(entry["id"]), entry["outcome"], frozenset(entry["stages"])
        else:
            header_prs, ident, outcome, stages = [int(p) for p in fields.get("PRs", "").split(",") if p.isdigit()], card.title, fields.get("Outcome", DEFAULT_OUTCOME), frozenset()
        bound, problem = evidence_numbers(kanban, card, repo)
        prs = tuple(sorted(set(bound or header_prs)))
        if prs or problem:
            items.append(Tracked(ident, kind, fields.get("Project", ""), repo, prs, outcome, stages, card, problem))
    return items


class CardSettler(Settler):
    def __init__(self, kanban: Kanban, loop: CardLoop, cards: list[Card], now: float) -> None:
        self.kanban, self.loop, self.now = kanban, loop, now
        self.status = {c.id: c.status for c in cards}

    def merge(self, item: Tracked, last: Seen) -> tuple[str, dict[str, object]]:
        card = item.card
        if card is None:
            return "refused", {"completion": "refused", "classification": "unknown", "detail": "the entry has no card"}
        if card.status != "done":
            if self.kanban.given_up_on_stale_claim(card):
                return "stale_blocked", {"card": card.id, "completion": "stale_blocked", "unblock": f"hermes kanban unblock {card.id}",
                                         "detail": "Kanban gave this card up after a stale kit claim; the owner or chief can run the unblock line, then loop reconcile completes it"}
            if not SHA.match(last.merge_commit) or not SHA.match(last.head):
                return "refused", {"card": card.id, "completion": "refused", "classification": "unknown", "detail": f"GitHub reports no merge commit for #{last.number} yet; run loop reconcile again"}
            open_units = [p for p in self.kanban.parents(card.id) if self.status.get(p) != "done"] if item.kind == "part" else []
            if open_units:
                return "waiting", {"card": card.id, "units": open_units}
            result = complete_card(self.kanban, card, item.outcome, Merged(last.number, last.url, last.head, last.merge_commit))
            if result["completion"] != "done":
                return "refused", {"card": result.pop("id"), **result}
        self.mark(item, card, "merged", last.number)
        return "done", {}

    def close(self, item: Tracked) -> None:
        if item.card is not None:
            let_go(self.kanban, item.card, "faker-kit: the pull request closed unmerged, claim released")
            self.mark(item, item.card, "abandoned", None)

    def mark(self, item: Tracked, card: Card, state: str, pr: int | None) -> None:
        if item.kind == "part":
            self.loop.update(int(item.ident), state, [pr] if pr else [], None, False, self.now)
            return
        header = card.header
        if header is not None:
            prs = sorted({*(int(p) for p in header.fields.get("PRs", "").split(",") if p), *([pr] if pr else [])})
            changed = header.with_fields({"State": state, "PRs": ",".join(str(p) for p in prs) or None, "Updated": str(float(self.now))})
            self.kanban.edit_body_if_changed(card.id, refresh(changed, card.title, item.repo), header)
        if state == "abandoned":
            self.kanban.archive(card.id)


class LoopSettler(Settler):
    def __init__(self, home: Path, bot: str, now: float) -> None:
        self.loop, self.now = Loop(home, bot), now

    def merge(self, item: Tracked, last: Seen) -> tuple[str, dict[str, object]]:
        self.loop.update(int(item.ident), "merged", [last.number], None, False, self.now)
        return "done", {}

    def close(self, item: Tracked) -> None:
        self.loop.update(int(item.ident), "abandoned", [], None, False, self.now)
