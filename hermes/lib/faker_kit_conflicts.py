from __future__ import annotations

import calendar
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from faker_kit_gh import Refused

KIT_OWNED = True

FOLLOWUP_TIMEOUT_SECONDS = 45 * 60
MAX_FOLLOWUPS = 2
CONFLICT_READS = 3
CONFLICT_PAUSE_SECONDS = 5.0
CONFLICT_WALL_SECONDS = 15.0
STAMP = "%Y-%m-%dT%H:%M:%SZ"
BRANCH = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,99}\Z")

NO_ACTION = "none"
REPOLL = "repoll"
STEER = "steer"
TELL_OWNER = "tell-owner"


@dataclass(frozen=True)
class Facts:
    number: int
    head: str
    draft: bool
    state: str
    fork: bool
    unit: str | None
    labeled: bool
    card_bound: bool
    mergeable: bool | None
    merge_state: str | None
    followups: tuple[tuple[str, float], ...]
    now: float


def parse_stamp(text: str) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(text, STAMP)))
    except ValueError:
        return None


def followup_times(rows: tuple[tuple[str, str], ...]) -> tuple[tuple[str, float], ...]:
    return tuple((head, at) for head, text in rows if (at := parse_stamp(text)) is not None)


def conflicting(mergeable: bool | None, merge_state: str | None) -> bool:
    return mergeable is False or str(merge_state or "").lower() == "dirty"


def unsettled(mergeable: bool | None, merge_state: str | None) -> bool:
    return mergeable is None or str(merge_state or "").lower() == "unknown"


def not_ours(facts: Facts) -> str | None:
    if facts.state != "open":
        return f"pull request is {facts.state}"
    if facts.fork:
        return "pull request comes from a fork"
    if not facts.unit:
        return "no `Unit: <id>` first line in the body"
    if not facts.labeled:
        return "not labeled faker-kit"
    if not facts.card_bound:
        return "no unit card of this project bot is bound to this pull request"
    return None


def decide(facts: Facts) -> str:
    if not_ours(facts) is not None:
        return NO_ACTION
    if not conflicting(facts.mergeable, facts.merge_state):
        return REPOLL if unsettled(facts.mergeable, facts.merge_state) else NO_ACTION
    here = [at for head, at in facts.followups if head == facts.head]
    if here:
        return TELL_OWNER if facts.now - max(here) >= FOLLOWUP_TIMEOUT_SECONDS else NO_ACTION
    return TELL_OWNER if len(facts.followups) >= MAX_FOLLOWUPS else STEER


def told_key(number: int, head: str) -> str:
    return f"{number}:{head}"


def owner_line(facts: Facts, base: str, reason: str) -> str:
    if reason == "timeout":
        return f"PR #{facts.number} still conflicting after the follow-up, head {facts.head}"
    if reason == "cap":
        return f"PR #{facts.number} conflicting again after {MAX_FOLLOWUPS} follow-ups, head {facts.head}"
    if reason == "no-session":
        return f"PR #{facts.number} conflicts with {base} and its unit has no session to ask, head {facts.head}"
    return f"PR #{facts.number} conflicts with {base} and the follow-up was not delivered, head {facts.head}"


def followup_message(number: int, base: str) -> str:
    target = base if BRANCH.match(base) else "the base branch"
    return (f"PR #{number} conflicts with {target}. Merge origin/{target} into your existing branch (a merge commit, never a rebase or force-push, unless the project rule in AGENTS.md says rebase), "
            "resolve the conflicts, rerun the checks you ran and push. Do not open a new pull request or branch.")


def read_settled(read: Callable[[int], dict], numbers: list[int], sleep: Callable[[float], None], clock: Callable[[], float]) -> tuple[dict[int, dict], dict[int, str]]:
    pulls: dict[int, dict] = {}
    unreadable: dict[int, str] = {}
    waiting = list(numbers)
    started, paused = clock(), 0.0
    for attempt in range(CONFLICT_READS):
        still = []
        for number in waiting:
            try:
                pulls[number] = read(number)
            except Refused as err:
                unreadable[number] = str(err)
                continue
            unreadable.pop(number, None)
            if unsettled(pulls[number].get("mergeable"), pulls[number].get("mergeable_state")) and not conflicting(pulls[number].get("mergeable"), pulls[number].get("mergeable_state")):
                still.append(number)
        waiting = still
        if not waiting or attempt == CONFLICT_READS - 1 or max(clock() - started, paused) + CONFLICT_PAUSE_SECONDS > CONFLICT_WALL_SECONDS:
            break
        sleep(CONFLICT_PAUSE_SECONDS)
        paused += CONFLICT_PAUSE_SECONDS
    return pulls, unreadable


def with_detail(facts: Facts, pull: dict) -> Facts:
    state = "merged" if pull.get("merged_at") else "closed" if pull.get("state") == "closed" else "open"
    head = str((pull.get("head") or {}).get("sha") or "")
    mergeable = pull.get("mergeable")
    return replace(facts, head=head, draft=bool(pull.get("draft")), state=state, mergeable=mergeable if isinstance(mergeable, bool) else None,
                   merge_state=str(pull["mergeable_state"]) if isinstance(pull.get("mergeable_state"), str) else None)
