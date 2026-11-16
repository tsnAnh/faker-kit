from __future__ import annotations

import re
import stat
from pathlib import Path

from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_loop import LoopError

MAX_TSV_BYTES = 2_000_000
MAX_BRIEF_BYTES = 200_000
MAX_SUMMARY = 300
MAX_UNIT_DECISIONS = 700
SESSION_URL = re.compile(r"\Ahttps://[^\s]{1,300}\Z")
BRIEF_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
GOAL_LINE = re.compile(r"\A[#*\s]*GOAL\b[:*\s-]*(.*)\Z")
DECISIONS_LINE = re.compile(r"\A[#*\s]*OWNER DECISIONS\b[:*\s-]*(.*)\Z")
SECTION_LINE = re.compile(r"\A[#*\s]*[A-Z][A-Z0-9 /_().-]{2,}:")


def read_units(path: Path) -> list[list[str]] | None:
    try:
        if path.stat().st_size > MAX_TSV_BYTES:
            raise LoopError(f"{path.name} is too large to migrate; nothing was changed")
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        raise LoopError(f"{path.name} cannot be read; nothing was changed") from None
    return [line.split("\t") for line in text.splitlines() if line.strip()]


def is_header_row(row: list[str]) -> bool:
    return len(row) > 1 and row[0] == "unit" and row[1].strip().lower() == "state"


def row_closure(state: str) -> str | None:
    state = state.strip()
    if state.startswith("superseded"):
        return "superseded"
    if state.startswith("folded") or state == "taken-back":
        return "folded"
    if state in ("merged", "abandoned", "closed", "done"):
        return "closed"
    return None


def unit_index(rows: list[list[str]] | None) -> tuple[dict[str, list[str]], set[str]]:
    found: dict[str, list[str]] = {}
    twice: set[str] = set()
    for row in rows or []:
        if len(row) < 2 or is_header_row(row):
            continue
        if row[0] in found:
            twice.add(row[0])
        found[row[0]] = row
    return found, twice


def brief_text(home: Path, bot: str, unit: str, row: list[str] | None) -> str | None:
    named = row[6].strip() if row is not None and len(row) > 6 else ""
    name = named if BRIEF_NAME.match(named) else f"{unit}.md"
    if not BRIEF_NAME.match(name):
        return None
    path = home / "briefs" / bot / name
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BRIEF_BYTES:
            return None
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def section(lines: list[str], pattern: re.Pattern[str]) -> str | None:
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match is None:
            continue
        parts = [match.group(1).strip()]
        for follow in lines[index + 1:]:
            if not follow.strip() or SECTION_LINE.match(follow):
                break
            parts.append(follow.strip())
        text = " ".join(" ".join(p for p in parts if p).split())
        return text or None
    return None


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def brief_source(home: Path, bot: str, unit: str, row: list[str] | None) -> tuple[str, str | None]:
    text = brief_text(home, bot, unit, row)
    if text is None:
        return unit, None
    lines = text.replace("\r\n", "\n").split("\n")
    goal = section(lines, GOAL_LINE)
    if goal is None:
        goal = next((" ".join(line.lstrip("#* ").split()) for line in lines if line.strip().lstrip("#* ")), None)
    decisions = section(lines, DECISIONS_LINE)
    if decisions is not None:
        decisions = cut(decisions, MAX_UNIT_DECISIONS)
        if not decisions.isprintable() or SECRET_SHAPES.search(decisions):
            decisions = None
    return (cut(goal, MAX_SUMMARY) if goal else unit), decisions
