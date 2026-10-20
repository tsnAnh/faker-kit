from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

LABEL = "agent:andon"
MARKER = "<!-- faker-kit:andon v1 -->"
MACHINE = "_machine"
MAX_REASON = 200
REASON = re.compile(r"\A[^\x00-\x1f\x7f]+\Z")


class AndonError(ValueError):
    pass


def clean_reason(words: list[str] | str) -> str:
    text = " ".join(" ".join(words).split() if isinstance(words, list) else str(words).split())
    if not text:
        raise AndonError("say why the line stops: andon raise <reason>")
    if not REASON.match(text):
        raise AndonError("the reason must be plain text on one line")
    return text[:MAX_REASON]


@dataclass(frozen=True)
class Cord:
    scope: str
    reason: str
    by: str
    at: float
    issues: dict[str, int]

    def describe(self) -> str:
        where = "this machine" if self.scope == "machine" else "this project"
        return f"andon pulled for {where} by {self.by}: {self.reason}"


class Andon:
    def __init__(self, home: Path) -> None:
        self.directory = home / "andon"

    def path(self, name: str) -> Path:
        return self.directory / f"{name}.json"

    def read(self, name: str) -> Cord | None:
        try:
            data = json.loads(self.path(name).read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("reason"):
            return None
        issues = data.get("issues")
        found = {str(k): int(v) for k, v in issues.items() if isinstance(v, int)} if isinstance(issues, dict) else {}
        at = data.get("at")
        return Cord("machine" if name == MACHINE else "project", str(data["reason"]), str(data.get("by") or "the owner"),
                    float(at) if isinstance(at, (int, float)) else 0.0, found)

    def write(self, name: str, cord: Cord) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path(name)
        tmp = target.with_name(f".{target.name}.{os.getpid()}")
        tmp.write_text(json.dumps({"reason": cord.reason, "by": cord.by, "at": cord.at, "issues": cord.issues}))
        tmp.replace(target)

    def drop(self, name: str) -> None:
        self.path(name).unlink(missing_ok=True)

    def pulled(self, bot: str | None) -> Cord | None:
        return self.read(MACHINE) or (self.read(bot) if bot else None)


def issue_body(scope: str, reason: str, how_to_clear: str) -> str:
    where = "every project on the Hermes machine" if scope == "machine" else "this project"
    return (f"{MARKER}\n"
            f"The line is stopped for {where}: {reason}\n\n"
            "Cloud agents working a faker-kit brief: stop where you are. Do not push, open, or update a pull request. "
            "Say that you stopped and name this issue, then end the session.\n\n"
            f"Nothing new starts until the owner clears the cord with `{how_to_clear}` in Hermes. "
            "Closing this issue on GitHub does not clear it.\n")
