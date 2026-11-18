from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path

from faker_kit_machine import routes, stale_route

SUBSCRIPTIONS_FILE = "webhook_subscriptions.json"
LOCK_FILE = ".webhook_subscriptions.lock"
MANAGED_BY = "faker-kit"
FILE_MODE = 0o600


class SubscriptionsError(Exception):
    pass


def kit_entries(bot: str, secret: str) -> dict[str, dict]:
    return {name: {**route, "secret": secret, "managed_by": MANAGED_BY} for name, route in routes(bot).items()}


def read_store(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as err:
        raise SubscriptionsError(f"cannot read {path.name}: {err.strerror}") from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise SubscriptionsError(f"{path.name} is not a JSON object, so the kit left it as it is")
    return data


def owned_stale(name: str, entry: object, bot: str) -> bool:
    return isinstance(entry, dict) and entry.get("managed_by") == MANAGED_BY and stale_route(name, bot)


def publish(path: Path, snapshot: dict[str, object]) -> None:
    text = json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".webhook_subscriptions_", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        tmp.replace(path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def sync_subscriptions(home: Path, bot: str, secret: str) -> bool:
    path = home / SUBSCRIPTIONS_FILE
    try:
        with (home / LOCK_FILE).open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = read_store(path)
            wanted = {name: entry for name, entry in current.items() if not owned_stale(name, entry, bot)}
            wanted.update(kit_entries(bot, secret))
            if wanted == current:
                return False
            publish(path, wanted)
            return True
    except OSError as err:
        raise SubscriptionsError(f"cannot write {path.name}: {err.strerror}") from None
