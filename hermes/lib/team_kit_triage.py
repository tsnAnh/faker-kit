"""Webhook triage for one project bot's route.

Reads one GitHub webhook payload on stdin. Prints `[SILENT]` unless the event is from one of the
bot's own repositories, changes the state of a dispatched unit, and its pull request or comment
is by a login in guard.yaml trusted_authors. Otherwise prints one JSON object whose `team_kit`
fields the route's prompt renders for that bot's team-kit-drain cron job. No model, no network.

Each project bot gets its own copy of the route's script, which names the bot it serves, so an
event can only ever wake the drain of the project that owns its repository.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from team_kit_contracts import REPORT_MARKER, brief_card, parse_pr_marker, report_status
from team_kit_guard.policy import PolicyError, load_policy

SILENT = "[SILENT]"


@dataclass(frozen=True)
class UnitEvent:
    event: str
    repo: str
    card: str
    issue: str = "-"
    pr: str = "-"
    status: str = "-"
    conclusion: str = "-"


@dataclass(frozen=True)
class Trust:
    repos: frozenset[str]
    authors: frozenset[str]

    def author(self, thing: dict) -> bool:
        return ((thing.get("user") or {}).get("login") or "").lower() in self.authors


def load_trust(bot: str) -> Trust:
    policy = load_policy()
    me = policy.bot(bot)
    if me.role != "project":
        raise PolicyError(f"{bot} is not a project bot")
    return Trust(repos=frozenset(r.lower() for r in me.repos), authors=policy.trusted_authors)


class PrIndex:
    """PR number to card and issue, learned from PR bodies, so check events can find their unit.

    One file per pull request, written atomically, so triage runs that overlap never lose an entry.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def _file(self, repo: str, pr: int) -> Path:
        return self.directory / f"{repo.lower().replace('/', '__')}__{int(pr)}.json"

    def remember(self, repo: str, pr: int, card: str, issue: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self._file(repo, pr)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(json.dumps({"card": card, "issue": issue}))
        tmp.replace(target)

    def lookup(self, repo: str, pr: int) -> dict | None:
        try:
            return json.loads(self._file(repo, pr).read_text())
        except (OSError, ValueError):
            return None


def classify(payload: dict, trust: Trust, index: PrIndex) -> UnitEvent | None:
    repo = (payload.get("repository") or {}).get("full_name") or ""
    if repo.lower() not in trust.repos:
        return None
    action = payload.get("action")

    if "pull_request" in payload and "comment" not in payload and "review" not in payload:
        pr = payload["pull_request"]
        marker = parse_pr_marker(pr.get("body") or "")
        head_repo = (((pr.get("head") or {}).get("repo")) or {}).get("full_name") or ""
        if not marker or not trust.author(pr) or head_repo.lower() != repo.lower():
            return None
        card, issue = marker
        number = int(pr["number"])
        if action in ("opened", "reopened", "ready_for_review"):
            index.remember(repo, number, card, issue)
            return UnitEvent("pr_opened", repo, card, str(issue), str(number))
        if action == "closed" and pr.get("merged"):
            return UnitEvent("pr_merged", repo, card, str(issue), str(number))
        return None

    if "comment" in payload and "issue" in payload and action == "created":
        issue = payload["issue"]
        card = brief_card(issue.get("body") or "")
        body = (payload["comment"].get("body") or "").lstrip()
        if not card or "pull_request" in issue or not body.startswith(REPORT_MARKER) or not trust.author(issue) or not trust.author(payload["comment"]):
            return None
        return UnitEvent("report", repo, card, str(issue["number"]), status=report_status(body) or "unreadable")

    if "check_suite" in payload and action == "completed":
        suite = payload["check_suite"]
        for pr in suite.get("pull_requests") or []:
            unit = index.lookup(repo, int(pr["number"]))
            if unit:
                return UnitEvent("checks_finished", repo, unit["card"], str(unit["issue"]), str(pr["number"]), conclusion=suite.get("conclusion") or "-")
        return None

    return None


def main(bot: str, stdin=None) -> int:
    payload = json.loads((stdin or sys.stdin).read() or "{}")
    try:
        trust = load_trust(bot)
    except PolicyError as err:
        print(f"team-kit triage: {err}", file=sys.stderr)
        print(SILENT)
        return 0
    found = classify(payload, trust, PrIndex(load_policy().home / "pr-cards"))
    print(json.dumps({"team_kit": asdict(found)}) if found else SILENT)
    return 0
