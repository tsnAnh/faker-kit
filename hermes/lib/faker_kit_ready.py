from __future__ import annotations

from collections.abc import Callable

from faker_kit_cards import unit_key
from faker_kit_gh import Gh, Refused, scrub
from faker_kit_kanban import Card, Kanban
from faker_kit_merge import from_fork, instant, label_state, unit_of
from faker_kit_reconcile import card_prs
from faker_kit_session_summary import closing_comments

READY_COMMAND = "faker-kit loop ready"
DRAFT_HOLD = (
    "#{number} is a draft, which is a hold: the kit marks a finished unit PR ready on the babysit tick; "
    "a draft that is still running or red stays one; the owner marks it ready or asks for the merge"
)
BULK_DRAFT_HOLD = "#{number} is a draft, which is a hold; a bulk merge never marks one ready: name it with --pr {number} --owner-approved, or the owner marks it ready"


def mark_ready(gh: Gh, number: int, head: str) -> str | None:
    fresh = gh.pull(number)
    if fresh.get("state") != "open" or fresh.get("merged_at"):
        return f"#{number} is no longer open"
    now_head = str((fresh.get("head") or {}).get("sha") or "")
    if now_head != head:
        return f"the head of #{number} moved from {head[:12]} to {now_head[:12]}; nothing was marked"
    if not fresh.get("draft"):
        return f"#{number} is already ready for review"
    node = str(fresh.get("node_id") or "")
    if not node:
        return f"GitHub reports no node id for #{number}"
    gh.mark_ready(node)
    return None


def closing_summary_after_head(pull: dict, comments: list[dict], machine_login: str, head_time: object) -> bool:
    pushed = instant(head_time)
    if pushed is None:
        return False
    return any((posted := instant(c.get("created_at"))) is not None and posted >= pushed for c in closing_comments(pull, comments, machine_login))


def unit_hold(pull: dict, repo: str, project: str, bot: str, cards: dict[str, Card], kanban: Kanban) -> tuple[str | None, Card | None]:
    if from_fork(pull, repo):
        return "comes from a fork", None
    unit = unit_of(pull)
    if unit is None:
        return "not a unit pull request: its body has no `Unit: <id>` first line", None
    state = label_state(pull)
    if state != "labeled":
        return f"not labeled faker-kit ({'labels unreadable' if state == 'unreadable' else 'no label'})", None
    card = cards.get(unit_key(project, unit))
    if card is None or card.header is None:
        return f"no unit card for {unit}", None
    if card.archived or card.status == "done":
        return f"unit card {card.id} is already {'archived' if card.archived else 'done'}", card
    if card.assignee != bot:
        return f"unit card {card.id} belongs to {card.assignee or 'nobody'}, not {bot}", card
    numbers, problem = card_prs(kanban, card, repo)
    if problem or int(pull["number"]) not in numbers:
        return f"unit card {card.id} is not bound to this pull request", card
    if card.status == "blocked":
        return f"unit card {card.id} is blocked: it waits on the owner", card
    return None, card


def judge(gh: Gh, kanban: Kanban, pull: dict, repo: str, project: str, bot: str, cards: dict[str, Card], machine_login: Callable[[], str],
          checks: Callable[[dict], str | None], stopped: str | None) -> str | None:
    reason, card = unit_hold(pull, repo, project, bot, cards, kanban)
    if reason is not None or card is None:
        return reason
    if stopped:
        return stopped
    number, head = int(pull["number"]), str((pull.get("head") or {}).get("sha") or "")
    comments = gh.pages(f"repos/{repo}/issues/{number}/comments")
    pushed = ((gh.commit(head).get("commit") or {}).get("committer") or {}).get("date")
    if not closing_summary_after_head(pull, comments, machine_login(), pushed) and kanban.live_run(card.id) is not None:
        return "the session is still running: no closing summary since the last push and the card has a live run"
    return checks(pull)


def ready_pass(gh: Gh, kanban: Kanban, bot: str, project: str, repo: str, machine_login: Callable[[], str], checks: Callable[[dict], str | None],
               write: Callable[[int, str], str | None], stopped: str | None, dry_run: bool) -> dict:
    cards = {c.key: c for c in kanban.cards(True) if c.key}
    marked: list[dict] = []
    would: list[dict] = []
    held: list[dict] = []
    errors: list[dict] = []
    for pull in gh.open_pulls():
        if not pull.get("draft"):
            continue
        number, head = int(pull["number"]), str((pull.get("head") or {}).get("sha") or "")
        unit = unit_of(pull)
        row = {"pr": number, "unit": unit, "head": head[:12]}

        try:
            reason = judge(gh, kanban, pull, repo, project, bot, cards, machine_login, checks, stopped)
        except Refused as err:
            errors.append({**row, "reason": scrub(str(err))})
            continue
        if reason is not None:
            held.append({**row, "reason": reason})
            continue
        if dry_run:
            would.append(row)
            continue
        try:
            moved = write(number, head)
        except Refused as err:
            errors.append({**row, "reason": scrub(str(err))})
            continue
        if moved is None:
            marked.append(row)
        else:
            held.append({**row, "reason": moved})
    flipped = would if dry_run else marked
    out: dict = {"dry_run": dry_run, "result": ("would mark" if dry_run else "marked") if flipped else "unchanged",
                 "marked": marked, "failed": errors, "lines": [f"marked #{r['pr']} ready for review (unit {r['unit']}, head {r['head']})" for r in marked]}
    if dry_run:
        out.update(would_mark=would, held=held)
    return out
