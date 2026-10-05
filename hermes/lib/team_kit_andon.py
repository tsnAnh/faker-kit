"""The andon cord: stop the line for the whole machine or for one project.

While a cord is pulled, nothing new starts: no coding agent, no verifier, no setup session.
Units already running keep running, and tick still reads their reports and opens gates, so
nothing is lost. A project's cord is also an open GitHub issue labelled `agent:andon`, which
every cloud agent checks before it pushes, so agents already at work stop at their next step.

The local record is the truth. Closing the GitHub issue alone never clears a cord, because a
cloud agent posts as the same GitHub user as the bot and could close it. Only `andon clear`
from Hermes does, which the owner asks for in Desktop.

  andon/_machine.json  the machine's cord, pulled with team-kit-admin andon raise. It also opens
                       an andon issue in every project's repository, so agents everywhere stop.
  andon/<bot>.json     a project's cord, pulled with team-kit-gh andon raise, or adopted by tick
                       from an andon issue the owner opened by hand on GitHub.

A bot name never starts with `_`, so no bot's file can be the machine's.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ANDON_LABEL = "agent:andon"
ANDON_MARKER = "<!-- team-kit:andon v1 -->"
MACHINE_MARKER = "<!-- team-kit:andon v1 scope=machine -->"
MACHINE = "_machine"
MAX_REASON = 200
REASON = re.compile(r"\A[^\x00-\x1f\x7f]+\Z")


class AndonError(ValueError):
    pass


def clean_reason(words: list[str] | str) -> str:
    text = " ".join(words) if isinstance(words, list) else str(words)
    text = " ".join(text.split())
    if not text:
        raise AndonError("say why the line stops: andon raise <reason>")
    if not REASON.match(text):
        raise AndonError("the reason must be plain text on one line")
    return text[:MAX_REASON]


@dataclass(frozen=True)
class Cord:
    scope: str  # "machine" or "project"
    reason: str
    by: str
    at: float
    issue: int | None = None

    def describe(self) -> str:
        where = "this machine" if self.scope == "machine" else "this project"
        ref = f" (#{self.issue})" if self.issue else ""
        return f"andon pulled for {where}{ref} by {self.by}: {self.reason}"


class Andon:
    def __init__(self, home: Path, now: Callable[[], float] = time.time):
        self.directory = home / "andon"
        self.now = now

    def path(self, name: str) -> Path:
        return self.directory / f"{name}.json"

    def read(self, name: str) -> dict | None:
        try:
            data = json.loads(self.path(name).read_text())
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("reason") else None

    def write(self, name: str, data: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.path(name)
        tmp = target.with_name(f".{target.name}.{os.getpid()}")
        tmp.write_text(json.dumps(data))
        tmp.replace(target)

    def drop(self, name: str) -> None:
        self.path(name).unlink(missing_ok=True)

    def cord(self, name: str, scope: str) -> Cord | None:
        data = self.read(name)
        if data is None:
            return None
        issue = data.get("issue")
        return Cord(scope, str(data["reason"]), str(data.get("by") or "the owner"), float(data.get("at") or 0),
                    int(issue) if isinstance(issue, int) else None)

    def machine(self) -> Cord | None:
        return self.cord(MACHINE, "machine")

    def project(self, bot: str) -> Cord | None:
        return self.cord(bot, "project")

    def pulled(self, bot: str | None = None) -> Cord | None:
        """The cord that stops this bot, the machine's first."""
        return self.machine() or (self.project(bot) if bot else None)


def issue_body(scope: str, reason: str, how_to_clear: str) -> str:
    marker = MACHINE_MARKER if scope == "machine" else ANDON_MARKER
    where = "every project on the Hermes machine" if scope == "machine" else "this project"
    return (f"{marker}\n"
            f"The line is stopped for {where}: {reason}\n\n"
            "Cloud agents working a team-kit brief: stop where you are. Do not push, open, or update a pull request. "
            "Post a `blocked` report that names this issue, then end the session.\n\n"
            f"Nothing new starts until the owner clears the cord with `{how_to_clear}` in Hermes. "
            "Closing this issue on GitHub does not clear it.\n")
