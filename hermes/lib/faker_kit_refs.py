from __future__ import annotations

import contextlib
import fcntl
import re
from collections.abc import Iterator
from pathlib import Path

from faker_kit_guard.t3code import SECRET_SHAPES
from faker_kit_kanban import REF, Card, KanbanError
from faker_kit_loop import Loop

PREFIXES = {
    "hermes-bot-mobile": "PAL",
    "fren": "FREN",
    "smilewallpaper": "SMILE",
    "charmeji": "CHARM",
    "memolly": "MEM",
    "paint_by_number": "PBN",
    "kith": "KITH",
    "data-warehouse": "DWH",
    "faker-kit": "FK",
}
PLACE = "PREFIXES in hermes/lib/faker_kit_refs.py"
MAX_TITLE = 70
LOCK = "card-refs.lock"
LOOSE_REF = re.compile(r"\A([A-Za-z]{2,5})-([0-9]{1,9})\Z")
SLUG = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")
EXAMPLE = "Admins can export the catalog as CSV"


def prefix_of(project: str) -> str:
    prefix = PREFIXES.get(project)
    if prefix is None:
        raise KanbanError(f"project {project} has no card prefix, so no card was created; add a row for it to {PLACE}")
    if not re.fullmatch(r"[A-Z]{2,5}", prefix):
        raise KanbanError(f"the card prefix {prefix!r} of project {project} is not 2 to 5 capital letters; fix {PLACE}")
    return prefix


def known_ref(text: str) -> str | None:
    found = LOOSE_REF.match(text.strip())
    if found is None or int(found.group(2)) < 1 or found.group(1).upper() not in PREFIXES.values():
        return None
    return f"{found.group(1).upper()}-{int(found.group(2))}"


def number_of(ref: str) -> int:
    found = REF.match(ref)
    return int(found.group(2)) if found else 0


def next_ref(cards: list[Card], project: str) -> str:
    prefix = prefix_of(project)
    top = max((number_of(c.ref) for c in cards if c.ref and c.header and c.header.fields.get("Project") == project), default=0)
    return f"{prefix}-{top + 1}"


def with_ref(ref: str, phrase: str) -> str:
    return f"{ref} {phrase}"


def clean_phrase(text: str | None, ref: str, slug: str | None = None, flag: str = "--title") -> str:
    room = MAX_TITLE - len(ref) - 1
    raw = " ".join((text or "").split())
    if not raw or not raw.isprintable():
        raise KanbanError(f'say what changes in a short English phrase on one line: {flag} "{EXAMPLE}"')
    if SLUG.match(raw) or (slug is not None and raw.lower() == slug.lower()):
        raise KanbanError(f'{flag} must say what changes for the user in plain words, not the unit id or a slug like "{raw}"; for example {flag} "{EXAMPLE}"')
    if len(raw) > room:
        raise KanbanError(f"the title phrase is {len(raw)} characters; {ref} plus the phrase must fit in {MAX_TITLE} characters, so the phrase has at most {room}")
    if SECRET_SHAPES.search(raw):
        raise KanbanError("the title phrase looks like it holds a token or key; say what changes in plain words")
    return raw


@contextlib.contextmanager
def numbered(home: Path, bot: str) -> Iterator[None]:
    with Loop(home, bot).locked():
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (home / LOCK).open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def find_ref(cards: list[Card], text: str) -> Card:
    ref = known_ref(text) or text.strip().upper()
    found = [c for c in cards if c.ref == ref]
    live = [c for c in found if not c.archived]
    if len(live) > 1 or (not live and len(found) > 1):
        raise KanbanError(f"two cards hold {ref}; nothing was changed")
    card = live[0] if live else (found[0] if found else None)
    if card is None:
        raise KanbanError(f"no card has the reference {ref}; nothing was changed")
    return card


def owner_of(card: Card) -> str:
    fields = card.header.fields if card.header else {}
    return fields.get("Bot") or card.assignee or "unknown"


def foreign(card: Card, project: str | None) -> bool:
    fields = card.header.fields if card.header else {}
    return project is not None and fields.get("Project") != project


def belongs_elsewhere(card: Card, ref: str) -> KanbanError:
    return KanbanError(f"{ref} belongs to the {owner_of(card)} bot; ask that bot")
