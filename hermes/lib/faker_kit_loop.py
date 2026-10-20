from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

STATES = ("handed-out", "in-progress", "waiting", "blocked", "ready-to-merge", "merged", "abandoned", "taken-back")
CLOSED = ("merged", "abandoned", "taken-back")
OWNER_NEWS = ("blocked", "ready-to-merge", *CLOSED)
MAX_PART = 300
REQUIRED = frozenset({"id", "project", "bot", "part", "state", "prs", "waits_on", "updated", "reported"})
MAX_PRS = 20
LINE_BREAKS = frozenset("\u0085\u2028\u2029")
WAITS_ON = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}#[1-9][0-9]{0,8}\Z")


class LoopError(ValueError):
    pass


def clean_part(text: str) -> str:
    raw = str(text).strip()
    if not raw:
        raise LoopError("say what the part is: handoff --project <project> --part <the part, in the owner's words>")
    if not raw.isprintable() or any(ch in LINE_BREAKS for ch in raw):
        raise LoopError("the part must be plain text on one line")
    part = " ".join(raw.split())
    if len(part) > MAX_PART:
        raise LoopError(f"the part must fit in {MAX_PART} characters; name the outcome, and let the project bot hold the detail")
    return part


def same_part(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


class Loop:
    def __init__(self, home: Path, bot: str) -> None:
        self.path = home / "loop" / f"{bot}.json"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_name(f"{self.path.name}.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return {"parts": [], "digest_at": None}
        except (OSError, ValueError):
            raise LoopError(f"the loop store {self.path.name} cannot be read; nothing was changed") from None
        parts = data.get("parts") if isinstance(data, dict) else None
        if not isinstance(parts, list) or not all(isinstance(p, dict) and p.keys() >= REQUIRED for p in parts):
            raise LoopError(f"the loop store {self.path.name} is not in the expected shape; nothing was changed")
        return data

    def write(self, data: dict) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def add(self, project: str, bot: str, text: str, now: float) -> dict:
        part = clean_part(text)
        with self.locked():
            data = self.read()
            held = next((p for p in data["parts"] if p["project"] == project and p["state"] not in CLOSED and same_part(p["part"], part)), None)
            if held:
                raise LoopError(f"part {held['id']} already holds this for {project} ({held['state']}); follow up with {held['bot']} about it instead of handing it out again")
            entry = {"id": max((int(p["id"]) for p in data["parts"]), default=0) + 1, "project": project, "bot": bot, "part": part,
                     "state": "handed-out", "prs": [], "waits_on": None, "added": now, "updated": now, "reported": None}
            data["parts"].append(entry)
            self.write(data)
        return entry

    def update(self, part_id: int, state: str | None, prs: list[int], waits_on: str | None, reported: bool, now: float) -> dict:
        if state is not None and state not in STATES:
            raise LoopError(f"state must be one of {', '.join(STATES)}")
        if any(not 0 < pr < 10**9 for pr in prs):
            raise LoopError("a pull request number must be a positive integer")
        if waits_on is not None and not WAITS_ON.match(waits_on):
            raise LoopError("--waits-on takes <project>#<pull request number>")
        with self.locked():
            data = self.read()
            entry = next((p for p in data["parts"] if p["id"] == part_id), None)
            if entry is None:
                raise LoopError(f"no part {part_id}; faker-kit loop lists them")
            next_state = state or entry["state"]
            if waits_on is not None and next_state != "waiting":
                raise LoopError("only a waiting part waits on a pull request; add --state waiting")
            if next_state == "waiting" and waits_on is None and not entry.get("waits_on"):
                raise LoopError("a waiting part names what it waits on: --waits-on <project>#<pull request number>")
            if state is not None and state != entry["state"]:
                entry["state"], entry["updated"], entry["reported"] = state, now, None
            if waits_on is not None:
                entry["waits_on"] = waits_on
            elif entry["state"] != "waiting":
                entry["waits_on"] = None
            merged = sorted({*entry["prs"], *prs})
            if len(merged) > MAX_PRS:
                raise LoopError(f"a part holds at most {MAX_PRS} pull requests")
            if merged != entry["prs"]:
                entry["prs"], entry["updated"] = merged, now
            if reported:
                entry["reported"] = entry["state"]
            self.write(data)
        return entry

    def show(self, everything: bool) -> dict:
        parts = self.read()["parts"]
        return {"open": [p for p in parts if p["state"] not in CLOSED] if not everything else parts,
                "to_report": [p for p in parts if p["state"] in OWNER_NEWS and p.get("reported") != p["state"]]}

    def digest(self, now: float) -> dict:
        with self.locked():
            data = self.read()
            since = data.get("digest_at")
            parts = data["parts"]
            out = {"since": since,
                   "closed": [p for p in parts if p["state"] in CLOSED and (since is None or p["updated"] > since)],
                   "in_flight": [p for p in parts if p["state"] not in CLOSED],
                   "waiting_on_owner": [p for p in parts if p["state"] in ("blocked", "ready-to-merge")]}
            data["digest_at"] = now
            self.write(data)
        return out
