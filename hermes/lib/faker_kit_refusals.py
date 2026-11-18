from __future__ import annotations

import contextlib
import fcntl
import json
import os
from collections.abc import Callable, Iterator
from pathlib import Path

BASE_HOLD = 900.0
GROWTH = 4
MAX_HOLD = 21600.0


def hold_for(count: int) -> float:
    return min(BASE_HOLD * GROWTH ** max(count - 1, 0), MAX_HOLD)


class Refusals:
    def __init__(self, home: Path, bot: str) -> None:
        self.path = home / "refusals" / f"{bot}.json"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_name(f"{self.path.name}.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(data, dict) else {}

    def write(self, data: dict[str, dict]) -> None:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(data, sort_keys=True))
        tmp.replace(self.path)

    def attempt(self, card_id: str, pr: int, head: str, now: float, complete: Callable[[], dict]) -> dict:
        with self.locked():
            data = self.read()
            last = data.get(card_id)
            same = last is not None and last.get("pr") == pr and last.get("head") == head and isinstance(last.get("result"), dict)
            count = last.get("count") if last is not None else None
            if same and last is not None and isinstance(count, int) and isinstance(last.get("at"), (int, float)):
                until = float(last["at"]) + hold_for(count)
                if now < until:
                    return {**last["result"], "held": True, "retry_after": until}
            result = complete()
            if result.get("completion") == "done" or "claim_left_open" in result:
                if data.pop(card_id, None) is not None:
                    self.write(data)
                return result
            repeated = same and last is not None and isinstance(count, int) and last["result"].get("classification") == result.get("classification")
            data[card_id] = {"pr": pr, "head": head, "at": now, "count": count + 1 if repeated and isinstance(count, int) else 1, "result": result}
            self.write(data)
            return result
