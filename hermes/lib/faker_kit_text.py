from __future__ import annotations

import time

from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_kanban import Header
from faker_kit_session_card import carry_sections

MAX_SUMMARY = 300
MAX_TEXT = 1800
MAX_SESSIONS = 5
WITHHELD = "[withheld: looks like a secret]"
NONE_RECORDED = "none recorded"
DECISIONS_PREFIX = "Owner decisions: "


def one_line(value: str) -> str:
    text = " ".join(str(value).split())
    return WITHHELD if SECRET_SHAPES.search(text) else text


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def summary_of(header: Header, default: str) -> str:
    first = header.text.split("\n", 1)[0] if header.text else ""
    return first or default


def stamp(value: str | None) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(value))) if value is not None else "unknown"
    except (ValueError, OverflowError, OSError):
        return "unknown"


def pull_numbers(header: Header) -> list[int]:
    found = {int(p) for p in header.fields.get("PRs", "").split(",") if p.isdigit()}
    found |= {int(p) for p in header.lines("PR") if p.isdigit()}
    return sorted(found)


def kept_decisions(header: Header) -> str | None:
    for line in header.text.split("\n"):
        if line.startswith(DECISIONS_PREFIX):
            value = line[len(DECISIONS_PREFIX):]
            return None if value == NONE_RECORDED else value
    return None


def closed_line(header: Header) -> str | None:
    for value in header.lines("History"):
        if value.startswith("closed ") and ": " in value:
            kind, _, reason = value[len("closed "):].partition(": ")
            return f"Closed ({one_line(kind)}): {one_line(reason)}"
    return None


def card_text(header: Header, summary: str, repo: str | None, decisions: str | None = None) -> str:
    fields = header.fields
    project, bot = one_line(fields.get("Project", "?")), one_line(fields.get("Bot", "?"))
    part = fields.get("Kind") == "part"
    if part:
        ident = header.key.rsplit(":", 1)[-1]
        second = f"Part {one_line(ident)} handed out to {bot} for {project}."
    else:
        serves = fields.get("Part", "")
        second = f"Unit of {project}, run by {bot}; serves " + (f"part {one_line(serves)}." if serves else "no chief part.")
    owner = one_line(fields.get("Decisions") or decisions or "") or NONE_RECORDED
    numbers = pull_numbers(header)
    pulls = ", ".join(f"https://github.com/{repo}/pull/{n}" if repo else f"#{n}" for n in numbers) or "none yet"
    sessions = [one_line(s) for s in header.lines("Session")][:MAX_SESSIONS]
    session_lines = [f"Sessions: {sessions[0]}", *(f"  {s}" for s in sessions[1:])] if sessions else ["Sessions: none yet"]
    waits = fields.get("Waits-on")
    status = f"Status: {one_line(fields.get('State', 'unknown'))}; " + (f"waits on {one_line(waits)}" if waits else "not waiting") + f"; updated {stamp(fields.get('Updated'))}"
    if part:
        status += f"; reported to the owner: {'yes' if fields.get('Reported') and fields.get('Reported') == fields.get('State') else 'no'}"
    lines = [cut(one_line(summary), MAX_SUMMARY), second, "", f"{DECISIONS_PREFIX}{owner}", f"Pull requests: {pulls}", *session_lines, status]
    closed = closed_line(header)
    if closed:
        lines.append(closed)
    return cut("\n".join(lines), MAX_TEXT)


def refresh(header: Header, title: str, repo: str | None, summary: str | None = None, decisions: str | None = None) -> Header:
    part = header.fields.get("Kind") == "part"
    chosen = title if part else (summary if summary is not None else summary_of(header, title))
    text = carry_sections(header.text, card_text(header, chosen, repo, decisions or kept_decisions(header)))
    return Header(header.fields, header.repeated, text, False)
