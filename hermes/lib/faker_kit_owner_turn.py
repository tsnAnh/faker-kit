from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from faker_kit_gh import Refused
from faker_kit_guard.policy import (
    FUTURE_SKEW_SECONDS,
    ORIGIN_OWNER,
    ORIGIN_UNSUPPORTED,
    OWNER_TURN_SECONDS,
    TURN_ID_ENV,
    Policy,
    owner_turn_lock,
    owner_turn_marker,
    write_owner_record,
)

TOO_OLD = "this Hermes is too old for owner-approved merges: it does not tell commands which turn they run in; update Hermes, then ask again"
NO_RECORD = "owner-approved merge runs only in a turn the owner typed in Hermes Desktop or the terminal UI, and this turn has no such record"
UNREADABLE = "owner-approved merge could not read this turn's record; ask again in a new message"


def read_record(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def find_record(folder: Path, turn: str) -> tuple[Path, dict] | None:
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.name.startswith("."):
            continue
        data = read_record(path)
        if data is not None and str(data.get("turn")) == turn:
            return path, data
    return None


def numbers(value: object) -> list[int]:
    if not isinstance(value, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise Refused(UNREADABLE)
    return list(value)


@dataclass(frozen=True)
class Claim:
    folder: Path
    path: Path
    turn: str
    pr: int

    def commit(self) -> None:
        with owner_turn_lock(self.folder):
            data = read_record(self.path)
            if data is None or str(data.get("turn")) != self.turn:
                return
            remaining = [named for named in numbers(data.get("merge_prs", [])) if named != self.pr]
            if not remaining:
                self.path.unlink(missing_ok=True)
                return
            data.update(merge_prs=remaining, inflight=[running for running in numbers(data.get("inflight", [])) if running != self.pr])
            write_owner_record(self.path, data)

    def release(self) -> None:
        with owner_turn_lock(self.folder):
            data = read_record(self.path)
            if data is None or str(data.get("turn")) != self.turn:
                return
            data["inflight"] = [running for running in numbers(data.get("inflight", [])) if running != self.pr]
            write_owner_record(self.path, data)


def claim_owner_turn(home: Path, asker: str, pr: int, project: str | None, policy: Policy, now: float, environ: Mapping[str, str], implied: bool = False) -> Claim:
    turn = (environ.get(TURN_ID_ENV) or "").strip()
    if not turn:
        raise Refused(TOO_OLD)
    folder = owner_turn_marker(home, asker)
    with owner_turn_lock(folder):
        found = find_record(folder, turn)
        if found is None:
            raise Refused(NO_RECORD)
        path, data = found
        origin = data.get("origin")
        if origin == ORIGIN_UNSUPPORTED:
            raise Refused(TOO_OLD)
        if origin != ORIGIN_OWNER:
            raise Refused(NO_RECORD)
        try:
            at = float(data["at"])
        except (KeyError, TypeError, ValueError):
            raise Refused(UNREADABLE) from None
        if at - now > FUTURE_SKEW_SECONDS:
            raise Refused("this turn's record is dated in the future, so it does not count; ask again in a new message")
        if now - at >= OWNER_TURN_SECONDS:
            raise Refused(f"the owner's request is more than {OWNER_TURN_SECONDS // 60} minutes old; ask again in a new message")
        named, running = numbers(data.get("merge_prs", [])), numbers(data.get("inflight", []))
        if named and pr not in named:
            raise Refused(f"the owner's message named {', '.join(f'#{n}' for n in named)}, not #{pr}; the owner names the pull request to merge")
        if pr in running or (not named and running):
            raise Refused("this turn's approval is already in use by a merge that has not finished or whose result was not confirmed; the owner asks again in a new message")
        if project is not None:
            check_project(project, data.get("projects_named"), policy, implied)
        data["inflight"] = [*running, pr]
        write_owner_record(path, data)
    return Claim(folder, path, turn, pr)


def check_project(project: str, named: object, policy: Policy, implied: bool = False) -> None:
    mentioned = [str(slug) for slug in named] if isinstance(named, list) else []
    if mentioned and project not in mentioned:
        raise Refused(f"the owner's message named {', '.join(mentioned)}, not {project}; merge in the project the owner named")
    if not mentioned and not implied and len(policy.projects()) > 1:
        raise Refused(f"the owner's message does not say which project; say which project ({', '.join(sorted(policy.projects()))}) to the owner and ask again")
