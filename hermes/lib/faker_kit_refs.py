from __future__ import annotations

import contextlib
import fcntl
import json
import os
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
    "titox-mobile-sdk": "TMS",
}
REGISTRY = "card-prefixes.json"
PREFIX_LOCK = "card-prefixes.lock"
PLACE = "PREFIXES in hermes/lib/faker_kit_refs.py"
PREFIX_SHAPE = re.compile(r"[A-Z]{2,5}")
MAX_TITLE = 70
LOCK = "card-refs.lock"
LOOSE_REF = re.compile(r"\A([A-Za-z]{2,5})-([0-9]{1,9})\Z")
SLUG = re.compile(r"\A[a-z0-9][a-z0-9_-]*\Z")
EXAMPLE = "Admins can export the catalog as CSV"


def registry(home: Path) -> dict[str, str]:
    path = home / REGISTRY
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) and PREFIX_SHAPE.fullmatch(v) for k, v in data.items()):
        raise KanbanError(f"the card prefix registry {path} cannot be read or is not a JSON object of project to 2 to 5 capital letters; "
                          "nothing was changed; repair that file by hand (keep its other entries), then run the command again")
    return data


def known_prefixes(home: Path) -> dict[str, str]:
    return {**registry(home), **PREFIXES}


def lookup_prefix(project: str, home: Path) -> str | None:
    return PREFIXES.get(project) or registry(home).get(project)


def prefix_of(project: str, home: Path) -> str:
    prefix = lookup_prefix(project, home)
    if prefix is None:
        raise KanbanError(f"project {project} has no card prefix, so no card was created; run faker-kit add-project for it, which records one in {home / REGISTRY}")
    if not PREFIX_SHAPE.fullmatch(prefix):
        raise KanbanError(f"the card prefix {prefix!r} of project {project} is not 2 to 5 capital letters; fix {PLACE}")
    return prefix


def letters_of(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper())


def candidates(project: str, extra: str) -> Iterator[str]:
    words = [w for w in (letters_of(w) for w in re.split(r"[-_.]+", project)) if w]
    joined = "".join(words)
    pool = joined if len(joined) >= 2 else joined + letters_of(extra)
    initials = "".join(w[0] for w in words)[:5]
    base = initials if len(initials) >= 2 else pool[:5]
    if len(base) < 2:
        return
    yield base
    for n in range(2, 6):
        yield ("".join([words[0][:n], *(w[0] for w in words[1:])]) if words else base)[:5]
    size = max(len(base), 2)
    for start in range(len(pool)):
        yield pool[start:start + size]
    for tail in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        yield base[:4] + tail
    for first in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        for second in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            yield base[:3] + first + second


def derive_prefix(project: str, taken: set[str], repo: str) -> str:
    owner, _, name = repo.partition("/")
    for option in candidates(project, f"{name}{owner}"):
        if PREFIX_SHAPE.fullmatch(option) and option not in taken:
            return option
    raise KanbanError(f"project {project} has too few letters for a card prefix; name the project with letters (add-project --project) and run it again")


@contextlib.contextmanager
def prefix_locked(home: Path) -> Iterator[None]:
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (home / PREFIX_LOCK).open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def write_registry(home: Path, data: dict[str, str]) -> None:
    path = home / REGISTRY
    tmp = path.with_name(f".{REGISTRY}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(data, sort_keys=True, indent=2) + "\n")
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def ensure_prefix(home: Path, project: str, repo: str) -> tuple[str, bool]:
    if project in PREFIXES:
        return PREFIXES[project], False
    with prefix_locked(home):
        held = registry(home)
        if project in held:
            return held[project], False
        prefix = derive_prefix(project, {*held.values(), *PREFIXES.values()}, repo)
        write_registry(home, {**held, project: prefix})
        return prefix, True


def known_ref(text: str, home: Path) -> str | None:
    found = LOOSE_REF.match(text.strip())
    if found is None or int(found.group(2)) < 1:
        return None
    prefix = found.group(1).upper()
    if prefix not in PREFIXES.values() and prefix not in registry(home).values():
        return None
    return f"{prefix}-{int(found.group(2))}"


def number_of(ref: str) -> int:
    found = REF.match(ref)
    return int(found.group(2)) if found else 0


def next_ref(cards: list[Card], project: str, home: Path) -> str:
    prefix = prefix_of(project, home)
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


def find_ref(cards: list[Card], text: str, home: Path) -> Card:
    ref = known_ref(text, home) or text.strip().upper()
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
