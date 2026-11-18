from __future__ import annotations

import calendar
import json
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from faker_kit_kanban import Card
from faker_kit_loop import CLOSED
from faker_kit_session_card import steered, steered_at
from faker_kit_text import kept_decisions, pull_numbers

KIT_OWNED = True

FILE = "pickup.json"
DEFAULT_PER_PROJECT = 5
DEFAULT_TOTAL = 20
STEER_GRACE_SECONDS = 3600
STAMP = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class Pickup:
    enabled: bool
    since: float | None = None
    by: str | None = None


def read_pickup(home: Path) -> Pickup:
    try:
        data = json.loads((home / FILE).read_text())
    except (OSError, ValueError):
        return Pickup(False)
    if not isinstance(data, dict) or data.get("enabled") is not True:
        return Pickup(False)
    since, by = data.get("since"), data.get("by")
    return Pickup(True, float(since) if isinstance(since, (int, float)) and not isinstance(since, bool) else None, by if isinstance(by, str) else None)


def write_pickup(home: Path, enabled: bool, by: str, now: float) -> Pickup:
    path = home / FILE
    temp = path.with_name(f".{FILE}.{os.getpid()}")
    try:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "w") as out:
            out.write(json.dumps({"enabled": enabled, "since": now, "by": by}))
        temp.replace(path)
    except OSError:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
        raise
    return Pickup(enabled, now, by)


def fields_of(card: Card) -> dict[str, str]:
    return card.header.fields if card.header else {}


def project_unit(card: Card, project: str) -> bool:
    fields = fields_of(card)
    return fields.get("Kind") == "unit" and fields.get("Project") == project and not card.archived and fields.get("State") not in CLOSED


def started(card: Card) -> bool:
    header = card.header
    return header is None or bool(header.fields.get("Run") or header.lines("Session") or pull_numbers(header))


def workable(card: Card) -> bool:
    header = card.header
    if header is None:
        return False
    goal = header.text.split("\n", 1)[0].strip()
    return kept_decisions(header) is not None and len(goal) > len(card.title)


def added_at(card: Card) -> float:
    try:
        return float(fields_of(card)["Added"])
    except (KeyError, ValueError):
        return float("inf")


def pickup_candidates(cards: list[Card], bot: str, project: str) -> list[Card]:
    found = [c for c in cards if c.assignee == bot and project_unit(c, project) and c.status == "ready" and not started(c) and c.header is not None
             and not steered(c.header) and workable(c)]
    return sorted(found, key=lambda c: (added_at(c), c.id))


def needs_brief(card: Card, bot: str, project: str) -> bool:
    return card.assignee == bot and project_unit(card, project) and card.status == "ready" and not started(card) and not workable(card)


def running_units(cards: list[Card], project: str | None = None) -> list[Card]:
    return [c for c in cards if c.status == "running" and not c.archived and fields_of(c).get("Kind") == "unit" and (project is None or fields_of(c).get("Project") == project)]


@dataclass(frozen=True)
class Slots:
    project: str
    in_project: list[Card]
    everywhere: list[Card]
    per_project: int
    total: int

    @property
    def free(self) -> int:
        return max(0, min(self.per_project - len(self.in_project), self.total - len(self.everywhere)))

    def refusal(self, unit: str) -> str | None:
        if self.free:
            return None
        def named(cards: list[Card]) -> str:
            return ", ".join(f"{c.id} {c.title}" for c in cards) or "none"
        return (f"launching {unit} would exceed the pickup limits, so nothing was launched: {self.project} runs {len(self.in_project)} of {self.per_project} unit cards ({named(self.in_project)}), "
                f"{len(self.everywhere)} of {self.total} in total ({named(self.everywhere)}); free slots: 0. Launch again when a running unit card finishes")


def slots(cards: list[Card], project: str, per_project: int | None, total: int) -> Slots:
    return Slots(project, running_units(cards, project), running_units(cards), DEFAULT_PER_PROJECT if per_project is None else per_project, total)


def abandoned_after_steer(cards: list[Card], bot: str, project: str, open_units: dict[tuple[str, str], dict], now: float) -> list[Card]:
    found = []
    for card in cards:
        header = card.header
        if card.assignee != bot or header is None or not project_unit(card, project) or card.status not in ("running", "ready") or not steered(header):
            continue
        if pull_numbers(header) or (project, card.title) in open_units:
            continue
        stamp = steered_at(header)
        try:
            asked = calendar.timegm(time.strptime(stamp, STAMP)) if stamp else None
        except ValueError:
            asked = None
        if asked is not None and now - asked >= STEER_GRACE_SECONDS:
            found.append(card)
    return found
