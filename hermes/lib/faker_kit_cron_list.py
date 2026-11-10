from __future__ import annotations

import re
from dataclasses import dataclass

KIT_OWNED = True

ANSI = re.compile(r"\x1b\[[0-9;]*m")
JOB_HEAD = re.compile(r"^\s*(\S+)\s+\[(active|paused|enabled|disabled)\]")
JOB_NAME_ROW = re.compile(r"^\s*Name:\s*(.*?)\s*$")
JOB_ROW = re.compile(r"^\s*(Schedule|Script|Mode|Last run):\s*(.*?)\s*$")
FINISHED_OUTCOMES = ("ok", "finished;", "ran, but the result was not delivered", "cancelled")


@dataclass(frozen=True)
class LastRun:
    at: str
    outcome: str

    @property
    def finished(self) -> bool:
        return self.outcome.startswith(FINISHED_OUTCOMES)


def cron_jobs(text: str) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    current: dict[str, object] = {}
    for line in ANSI.sub("", text).splitlines():
        head = JOB_HEAD.match(line)
        if head:
            current = {"id": head.group(1), "enabled": head.group(2) in ("active", "enabled")}
            continue
        name = JOB_NAME_ROW.match(line)
        if name and current:
            jobs[name.group(1)] = current
            continue
        row = JOB_ROW.match(line)
        if row and current:
            current[row.group(1).lower().replace(" ", "_")] = row.group(2)
    return jobs


def last_run(job: dict) -> LastRun | None:
    parts = str(job.get("last_run") or "").split(None, 1)
    return LastRun(parts[0], parts[1] if len(parts) > 1 else "") if parts else None
