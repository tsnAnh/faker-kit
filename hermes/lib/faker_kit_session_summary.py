from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from faker_kit_gh import TOKEN_SHAPES, Gh
from faker_kit_guard.policy import wrap_untrusted
from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_incidents import fence, redact
from faker_kit_merge import PASSING_CONCLUSIONS, unit_of

MARKER = "Closing summary:"
MAX_SUMMARY_CHARS = 2000
NO_PULL_REASON = "no pull request"
NO_SUMMARY_REASON = "no Closing summary comment by the pull request's author or this machine's login, and the body holds none"
UNREADABLE = "no readable summary"
STEER_MESSAGE = (
    "The time for this task is up. Before you end, post a pull request comment whose first line is exactly `Closing summary:` "
    "followed by four short labelled parts: Done, Skipped, Open questions, Blockers. If you could not finish a pull request, "
    "open a draft pull request whose body begins with `Unit: <unit-id>` and carries the same summary. Do not start new work."
)
BODY_SUMMARY = re.compile(r"(?m)^" + re.escape(MARKER))


@dataclass(frozen=True)
class Found:
    source: str
    text: str | None
    reason: str | None


def clean(text: str) -> str:
    return fence(redact(redact(text, (), SECRET_SHAPES), (), TOKEN_SHAPES), MAX_SUMMARY_CHARS)


def pick(pull: dict, comments: list[dict], machine_login: str) -> Found:
    author = str((pull.get("user") or {}).get("login") or "").lower()
    trusted = {name for name in (author, machine_login.lower()) if name}
    said = [str(c.get("body") or "") for c in comments if str(c.get("body") or "").startswith(MARKER) and str((c.get("user") or {}).get("login") or "").lower() in trusted]
    if said:
        return Found("comment", said[-1], None)
    body = str(pull.get("body") or "")
    if pull.get("draft") and body.strip():
        return Found("body", body, None)
    start = BODY_SUMMARY.search(body)
    if start:
        return Found("body", body[start.start():], None)
    return Found("none", None, NO_SUMMARY_REASON)


def check_result(runs: list[dict]) -> dict[str, int]:
    done = [str(run.get("conclusion") or "") for run in runs]
    passed = sum(1 for c in done if c in PASSING_CONCLUSIONS)
    pending = sum(1 for c in done if not c)
    return {"passed": passed, "failed": len(done) - passed - pending, "pending": pending}


def unreadable(unit: str | None, reason: str, number: int | None = None) -> dict:
    return {"unit": unit, "pr": number, "head": None, "state": None, "draft": None, "checks": None, "summary": None, "source": "none", "readable": False,
            "reason": reason, "result": UNREADABLE, "text": None}


def read_summary(gh: Gh, unit: str | None, number: int | None, machine_login: Callable[[], str]) -> dict:
    if number is None:
        return unreadable(unit, NO_PULL_REASON)
    pull = gh.pull(number)
    head = str((pull.get("head") or {}).get("sha") or "")
    state = "merged" if pull.get("merged_at") else "closed" if pull.get("state") == "closed" else "open"
    found = pick(pull, gh.pages(f"repos/{gh.repo}/issues/{number}/comments"), machine_login())
    text = clean(found.text) if found.text else None
    report = {"unit": unit or unit_of(pull), "pr": number, "head": head, "state": state, "draft": bool(pull.get("draft")), "checks": check_result(gh.check_runs(head)) if head else None,
              "summary": wrap_untrusted(f"github:{gh.repo}#{number}:{found.source}", text) if text else None, "source": found.source, "readable": bool(text), "text": text}
    return report if text else {**report, "reason": found.reason, "result": UNREADABLE}
