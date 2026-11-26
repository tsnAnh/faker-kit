from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from faker_kit_kanban import Card

PASS_BUDGET_SECONDS = 25.0
LOOP_BUDGET_ENV = "FAKER_KIT_PASS_BUDGET_SECONDS"
CLAIMS_SHARE = 0.8


def pass_seconds() -> float:
    try:
        seconds = float(os.environ.get(LOOP_BUDGET_ENV) or PASS_BUDGET_SECONDS)
    except ValueError:
        return PASS_BUDGET_SECONDS
    return seconds if seconds > 0 else PASS_BUDGET_SECONDS


@dataclass(frozen=True)
class Budget:
    clock: Callable[[], float]
    deadline: float

    @classmethod
    def start(cls, clock: Callable[[], float], seconds: float | None = None) -> Budget:
        return cls(clock, clock() + (pass_seconds() if seconds is None else seconds))

    def spent(self) -> bool:
        return self.clock() >= self.deadline

    def share(self, fraction: float) -> Budget:
        now = self.clock()
        return Budget(self.clock, now + max(self.deadline - now, 0.0) * fraction)


class Cursor:
    def __init__(self, home: Path, bot: str, kind: str) -> None:
        self.path = home / "passes" / bot / f"{kind}.json"
        self.seen = self.read()
        self.round = max(self.seen.values(), default=0.0) + 1

    def read(self) -> dict[str, float]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: float(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(data, dict) else {}

    def order(self, cards: list[Card]) -> list[Card]:
        return sorted(cards, key=lambda c: self.seen.get(c.id, 0.0))

    def keep_only(self, cards: list[Card]) -> None:
        ids = {c.id for c in cards}
        self.seen = {k: v for k, v in self.seen.items() if k in ids}

    def mark(self, card: Card) -> None:
        self.seen[card.id] = self.round
        self.write()

    def write(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(self.seen, sort_keys=True))
        tmp.replace(self.path)


@dataclass
class Reach:
    candidates: int = 0
    reached: int = 0
    unreached: tuple[str, ...] = ()

    @property
    def partial(self) -> bool:
        return bool(self.unreached)

    def view(self) -> dict[str, object]:
        return {"partial": self.partial, "candidates": self.candidates, "reached": self.reached, "unreached": list(self.unreached)}
