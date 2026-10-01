#!/usr/bin/env python3
"""Hermes webhook script for the team-kit-github route.

Reads one GitHub webhook payload on stdin. Prints `[SILENT]` unless the event changes the
state of a dispatched unit and its pull request or comment is by a login in
guard.yaml trusted_authors. Otherwise prints one JSON object whose `team_kit` fields the
route's prompt renders for the dispatcher-drain cron job. No model, no network.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from team_kit_contracts import REPORT_MARKER, brief_card, parse_pr_marker, report_status  # noqa: E402

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


def home() -> Path:
    return Path(os.environ.get("TEAM_KIT_HOME") or Path.home() / ".hermes" / "team-kit")


@dataclass(frozen=True)
class Trust:
    repos: frozenset[str]
    authors: frozenset[str]

    def author(self, thing: dict) -> bool:
        return ((thing.get("user") or {}).get("login") or "").lower() in self.authors


def load_trust() -> Trust:
    import yaml

    config = yaml.safe_load((home() / "guard.yaml").read_text()) or {}
    lower = lambda key: frozenset(str(v).lower() for v in config.get(key) or [])  # noqa: E731
    return Trust(repos=lower("repos"), authors=lower("trusted_authors"))


class PrIndex:
    """PR number to card and issue, learned from PR bodies, so check events can find their unit."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def remember(self, repo: str, pr: int, card: str, issue: int) -> None:
        self.data[f"{repo.lower()}#{pr}"] = {"card": card, "issue": issue}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, sort_keys=True))
        tmp.replace(self.path)

    def lookup(self, repo: str, pr: int) -> dict | None:
        return self.data.get(f"{repo.lower()}#{pr}")


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


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    found = classify(payload, load_trust(), PrIndex(home() / "pr-cards.json"))
    print(json.dumps({"team_kit": asdict(found)}) if found else SILENT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
