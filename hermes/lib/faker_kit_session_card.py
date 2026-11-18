from __future__ import annotations

import re
from dataclasses import replace

from faker_kit_kanban import Header

SECTION_TITLE = "Session summary (a claim, not proof):"
SECTION_END = "End of session summary."
STEER_TITLE = "Session steer:"
STORED = re.compile(re.escape(SECTION_TITLE) + r"\nRead at (\S+) at head (\S+)\n(.*?)\n" + re.escape(SECTION_END), re.S)
STEERED = re.compile(re.escape(STEER_TITLE) + r" asked session (\S+) at (\S+) for a closing summary\.")


def appended(text: str, section: str) -> str:
    return (text.rstrip("\n") + "\n\n" if text.strip() else "") + section


def carry_sections(old: str, new: str) -> str:
    for found in (STORED.search(old), STEERED.search(old)):
        if found and found.group(0) not in new:
            new = appended(new, found.group(0))
    return new


def with_summary(header: Header, text: str, head: str, read_at: str) -> Header | None:
    held = STORED.search(header.text)
    if held and held.group(2) == head and held.group(3) == text:
        return None
    section = f"{SECTION_TITLE}\nRead at {read_at} at head {head}\n{text}\n{SECTION_END}"
    if held:
        return replace(header, text=header.text[:held.start()] + section + header.text[held.end():])
    return replace(header, text=appended(header.text, section))


def steered(header: Header) -> bool:
    return STEERED.search(header.text) is not None


def with_steer(header: Header, session: str, at: str) -> Header:
    return replace(header, text=appended(header.text, f"{STEER_TITLE} asked session {session} at {at} for a closing summary."))


def steered_at(header: Header) -> str | None:
    found = STEERED.search(header.text)
    return found.group(2) if found else None
