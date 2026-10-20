from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

from faker_kit_gh import Gh, Refused

LABEL_FIX = "faker:fix"
LABELS = {
    LABEL_FIX: ("0e8a16", "A collaborator asks the project bot to reproduce this bug and fix it"),
    "faker:not-reproduced": ("fbca04", "The bot could not reproduce this bug; the evidence is in a comment"),
    "faker:expected": ("c5def5", "The bot found this is the intended behavior; the evidence is in a comment"),
}
WRITE_ROLES = ("admin", "maintain", "write")
WRITE_PERMISSIONS = ("admin", "write")
LOGIN = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z")
MAX_LISTED = 20


class IssueStore:
    def __init__(self, home: Path, bot: str) -> None:
        self.path = home / "issues" / f"{bot}.json"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_name(f"{self.path.name}.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def claimed(self) -> dict[str, int]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        rows = data.get("claimed") if isinstance(data, dict) else None
        return {str(k): v for k, v in rows.items() if isinstance(v, int)} if isinstance(rows, dict) else {}

    def claim(self, number: int, event: int) -> None:
        rows = self.claimed()
        rows[str(number)] = event
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps({"claimed": rows}))
        tmp.replace(self.path)


def latest_fix_label(events: list[dict]) -> dict | None:
    labeled = [e for e in events if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == LABEL_FIX]
    return labeled[-1] if labeled else None


def may_fix(permission: dict) -> bool:
    return str(permission.get("role_name") or "") in WRITE_ROLES or str(permission.get("permission") or "") in WRITE_PERMISSIONS


class Reports:
    def __init__(self, gh: Gh, store: IssueStore) -> None:
        self.gh, self.store = gh, store

    def collaborator(self, login: str) -> bool:
        if not LOGIN.match(login):
            return False
        try:
            return may_fix(self.gh.json(f"repos/{self.gh.repo}/collaborators/{login}/permission"))
        except Refused as err:
            if "HTTP 404" in str(err):
                return False
            raise

    def entry(self, issue: dict) -> dict | None:
        number = int(issue["number"])
        if "pull_request" in issue or issue.get("state") != "open":
            return None
        event = latest_fix_label(self.gh.pages(f"repos/{self.gh.repo}/issues/{number}/events"))
        login = str(((event or {}).get("actor") or {}).get("login") or "")
        if event is None or not isinstance(event.get("id"), int) or not self.collaborator(login):
            return None
        return {"number": number, "labeler": login, "url": f"https://github.com/{self.gh.repo}/issues/{number}", "event": event["id"]}

    def eligible(self) -> list[dict]:
        issues = self.gh.pages(f"repos/{self.gh.repo}/issues", {"labels": LABEL_FIX, "state": "open"})
        claimed = self.store.claimed()
        found = []
        for issue in sorted(issues, key=lambda i: int(i["number"])):
            row = self.entry(issue)
            if row and claimed.get(str(row["number"])) != row["event"]:
                found.append(row)
            if len(found) == MAX_LISTED:
                break
        return found

    def claim(self, number: int) -> dict:
        with self.store.locked():
            row = self.entry(self.gh.json(f"repos/{self.gh.repo}/issues/{int(number)}"))
            if row is None:
                raise Refused(f"#{number} is not an open issue whose {LABEL_FIX} label a collaborator with write access added")
            self.store.claim(number, row["event"])
        return {"claimed": number, "labeler": row["labeler"], "url": row["url"]}
