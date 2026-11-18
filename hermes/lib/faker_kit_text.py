from __future__ import annotations

from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_kanban import Header
from faker_kit_session_card import carry_sections

MAX_SUMMARY = 300
MAX_TEXT = 1800
WITHHELD = "[withheld: looks like a secret]"
NONE_RECORDED = "none recorded"
NONE = "none"
GOAL_PREFIX = "**Goal:** "
DONE_PREFIX = "**Done means:** "
STATUS_PREFIX = "**Current status:** "
DECISIONS_PREFIX = "**Owner decisions:** "
OLD_DECISIONS_PREFIX = "Owner decisions: "
DONE_MEANS = {
    "merged-pr": "One pull request is merged after every CI check passes on its head.",
    "built": "A built artifact is recorded as evidence on this card.",
    "installed": "The build is installed on a device and this card records what was read back.",
    "verified": "The result is verified and this card records the receipt.",
}


def one_line(value: str) -> str:
    text = " ".join(str(value).split())
    return WITHHELD if SECRET_SHAPES.search(text) else text


def cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def summary_of(header: Header, default: str) -> str:
    for line in header.text.split("\n"):
        if line.startswith(GOAL_PREFIX):
            return line[len(GOAL_PREFIX):] or default
    first = header.text.split("\n", 1)[0] if header.text else ""
    return first or default


def pull_numbers(header: Header) -> list[int]:
    found = {int(p) for p in header.fields.get("PRs", "").split(",") if p.isdigit()}
    found |= {int(p) for p in header.lines("PR") if p.isdigit()}
    return sorted(found)


def kept_decisions(header: Header) -> str | None:
    for line in header.text.split("\n"):
        for prefix in (DECISIONS_PREFIX, OLD_DECISIONS_PREFIX):
            if line.startswith(prefix):
                value = line[len(prefix):]
                return None if value in (NONE_RECORDED, NONE) else value
    return None


def closed_line(header: Header) -> str | None:
    for value in header.lines("History"):
        if value.startswith("closed ") and ": " in value:
            kind, _, reason = value[len("closed "):].partition(": ")
            return f"Closed ({one_line(kind)}): {one_line(reason)}"
    return None


def card_text(header: Header, goal: str, repo: str | None, decisions: str | None = None) -> str:
    fields = header.fields
    part = fields.get("Kind") == "part"
    owner = one_line(fields.get("Decisions") or decisions or "") or NONE
    pulls = ", ".join(f"https://github.com/{repo}/pull/{n}" if repo else f"#{n}" for n in pull_numbers(header))
    waits = fields.get("Waits-on")
    status = [one_line(fields.get("State", "unknown")).replace("-", " ").capitalize()]
    if waits:
        status.append(f"waits on {one_line(waits)}")
    if pulls:
        status.append(f"pull request: {pulls}")
    if part and fields.get("Work"):
        status.append(one_line(fields["Work"]))
    closed = closed_line(header)
    if closed:
        status.append(closed)
    paragraphs = [*([f"Ref: {header.ref}"] if header.ref else []), f"{GOAL_PREFIX}{cut(one_line(goal), MAX_SUMMARY)}",
                  f"{DONE_PREFIX}{DONE_MEANS.get(fields.get('Outcome', ''), DONE_MEANS['merged-pr'])}", f"{STATUS_PREFIX}{'; '.join(status)}", f"{DECISIONS_PREFIX}{owner}"]
    return cut("\n\n".join(paragraphs), MAX_TEXT)


def refresh(header: Header, fallback: str, repo: str | None, summary: str | None = None, decisions: str | None = None) -> Header:
    goal = summary if summary is not None else summary_of(header, fallback)
    text = carry_sections(header.text, card_text(header, goal, repo, decisions or kept_decisions(header)))
    return Header(header.fields, header.repeated, text, False)
