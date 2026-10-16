"""One review request per final head.

When a repository names a review_app, team-kit-gh asks that app's review workflow to review a unit's pull request once the
unit's agent reports done on the exact head the pull request still has. An intermediate push never gets a request, and
each (pull request, head, base) gets at most one. This module decides; team-kit-gh reads GitHub, holds the unit's lock,
and makes the call.

A report is a comment written with the same GitHub login the agents use, and it is not signed. Its authority is only what
code checks here: the author's login, the unit marker on the pull request, the pull request's repository and branch, and
the exact head. No comment text decides anything beyond the report's header lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

SHA = re.compile(r"\A[0-9a-f]{40}\Z")
REVIEW_WORKFLOW = "claude-review.yml"  # the review app's workflow, started with workflow_dispatch on the default branch
REQUEST_ATTEMPTS = 3  # calls GitHub refused before team-kit-gh stops asking for this head and tells the owner
RETRY_SECONDS = 600


@dataclass(frozen=True)
class Report:
    """The latest trusted report since the unit's latest launch, as team-kit-gh parsed its header."""

    by: str
    status: str
    pr: int | None
    head: str | None
    at: float | None


@dataclass(frozen=True)
class Pull:
    """A pull request as GitHub reports it now."""

    number: int
    state: str
    head: str
    base: str
    base_ref: str
    branch: str
    same_repo: bool
    draft: bool
    mergeable: bool | None
    marker_issue: int | None


@dataclass(frozen=True)
class Key:
    pr: int
    head: str
    base: str


Step = Literal["send", "hold", "lost"]


def unready(unit: int, report: Report, pull: Pull | None, branch: str | None, default_branch: str, checks: str, cord: bool,
            trusted: frozenset[str], now: float, no_checks_grace: float) -> str | None:
    """Why this unit's pull request may not get a review request now, or None when it may."""
    if cord:
        return "the andon cord is pulled"
    if report.status != "done":
        return f"the latest report is {report.status}, not done"
    if report.by.lower() not in trusted:
        return "the latest report is not from a login in trusted_dispatchers"
    if not report.head or not SHA.match(report.head):
        return "the done report names no full head commit"
    if pull is None or pull.number != report.pr:
        return "the done report names no pull request of this repository"
    if pull.state != "open":
        return f"#{pull.number} is {pull.state}"
    if not pull.same_repo:
        return f"#{pull.number} comes from a fork"
    if pull.marker_issue != unit:
        return f"#{pull.number} does not carry this unit's marker"
    if not branch or pull.branch != branch:
        return f"#{pull.number} is not on the unit's branch"
    if pull.base_ref != default_branch:
        return f"#{pull.number} does not target the default branch"
    if pull.draft:
        return f"#{pull.number} is a draft"
    if not SHA.match(pull.head) or not SHA.match(pull.base):
        return f"GitHub reports no head or base commit for #{pull.number}"
    if pull.head != report.head:
        return f"#{pull.number} moved to {pull.head[:12]} after the done report on {report.head[:12]}"
    if pull.mergeable is None:
        return f"GitHub is still checking #{pull.number} for conflicts"
    if not pull.mergeable:
        return f"#{pull.number} conflicts with its base branch"
    if checks == "none":
        if report.at is None or now - report.at < no_checks_grace:
            return "no checks have registered yet"
    elif checks != "passed":
        return f"checks are {checks}"
    return None


def key_of(record: dict[str, object] | None) -> Key | None:
    if not record:
        return None
    pr, head, base = record.get("pr"), record.get("head"), record.get("base")
    if not isinstance(pr, int) or not isinstance(head, str) or not isinstance(base, str):
        return None
    return Key(pr, head, base)


def step(record: dict[str, object] | None, key: Key, now: float) -> Step:
    """What to do for a ready head, given the unit's record.

    send: no request for this key yet, or a refused one is due a retry.
    lost: an earlier process stopped between writing `sending` and learning GitHub's answer. GitHub may have the request,
    so it is never repeated; the owner is told instead.
    hold: requested, lost, given up, or a retry not yet due."""
    if record is None or key_of(record) != key:
        return "send"
    state = record.get("state")
    if state == "sending":
        return "lost"
    if state == "failed":
        attempts, at = record.get("attempts"), record.get("at")
        due = isinstance(at, (int, float)) and now - at >= RETRY_SECONDS
        if isinstance(attempts, int) and attempts < REQUEST_ATTEMPTS and due:
            return "send"
    return "hold"
