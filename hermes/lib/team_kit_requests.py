"""Chief project requests: the chief asks the bootstrap bot, through local files, to set up a new project."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REQUEST_TTL_SECONDS = 7 * 24 * 3600


class RequestError(ValueError):
    pass


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _stamp(seconds: float) -> str:
    return time.strftime("%Y%m%dt%H%M%Sz", time.gmtime(seconds))


def _remove(path: Path) -> None:
    """Delete one bad or expired entry of our own private folder; never let one stuck entry hide the rest."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


# A request is structured (a repository and
# two names), never free text, so nothing the chief writes can steer the bootstrap bot beyond those three values.
REQUEST_NAME = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,63}\Z")
REQUEST_REPO = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
REQUEST_ID_PATTERN = r"[0-9]{8}t[0-9]{6}z-[a-z0-9][a-z0-9_-]{0,71}"
REQUEST_ID = re.compile(rf"\A{REQUEST_ID_PATTERN}\Z")
REQUEST_FIELDS = ("id", "bootstrap", "repo", "project", "bot", "from")


@dataclass(frozen=True)
class ProjectRequest:
    path: Path
    data: dict[str, object]

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def expires_at(self) -> float:
        expires = self.data["expires_at"]
        return float(expires) if isinstance(expires, (int, float, str)) else 0.0

    def field(self, key: str) -> str:
        return str(self.data[key])


def requests_dir(home: Path, bootstrap: str) -> Path:
    return home / "project-requests" / bootstrap


@contextlib.contextmanager
def request_lock(home: Path, bootstrap: str) -> Iterator[None]:
    """One writer at a time on a bootstrap bot's requests: the chief, the inbox job, and setup-request."""
    directory = requests_dir(home, bootstrap)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    with (directory / ".lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _write_record(path: Path, data: dict[str, object]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    tmp.replace(path)


def _read_request(path: Path, bootstrap: str) -> ProjectRequest | None:
    """The record, or None if it is corrupt: unreadable, oversized, the wrong shape, or not this bootstrap bot's."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("kind") != "project":
        return None
    if not all(isinstance(data.get(k), str) for k in REQUEST_FIELDS):
        return None
    if data["id"] != path.stem or data["bootstrap"] != bootstrap or not REQUEST_ID.match(data["id"]):
        return None
    if not (REQUEST_REPO.match(data["repo"]) and all(REQUEST_NAME.match(data[k]) for k in ("project", "bot", "from", "bootstrap"))):
        return None
    try:
        created, expires = float(data["created_at"]), float(data["expires_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not created < expires <= created + REQUEST_TTL_SECONDS:
        return None
    return ProjectRequest(path, data)


def active_requests(home: Path, bootstrap: str, now: float, prune: bool = True) -> list[ProjectRequest]:
    """Unexpired requests, oldest first. With prune, expired and corrupt records are deleted on the way."""
    directory = requests_dir(home, bootstrap)
    if not directory.is_dir():
        return []
    active: list[ProjectRequest] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name):
        request = _read_request(path, bootstrap)
        if request is None or now >= request.expires_at:
            if prune:
                _remove(path)
            continue
        active.append(request)
    return active


def write_project_request(home: Path, bootstrap: str, repo: str, project: str, bot: str, from_bot: str, now: float) -> dict[str, object]:
    if not REQUEST_REPO.match(repo or ""):
        raise RequestError(f"--repo {repo!r} must be OWNER/REPO")
    for value, what in ((project, "--project"), (bot, "--bot")):
        if not REQUEST_NAME.match(value or "") or value == "default":
            raise RequestError(f"{what} {value!r} must be lowercase letters, digits, - and _, starting with a letter or digit, and not default")
    if not REQUEST_NAME.match(from_bot or ""):
        raise RequestError(f"{from_bot!r} is not a bot name")
    with request_lock(home, bootstrap):
        for request in active_requests(home, bootstrap, now):
            for key, value in (("repo", repo.lower()), ("project", project), ("bot", bot)):
                if request.field(key).lower() == value:
                    raise RequestError(f"request {request.id} waiting for {bootstrap} already names {key} {request.field(key)}")
        return _new_request(home, bootstrap, repo, project, bot, from_bot, now)


def _new_request(home: Path, bootstrap: str, repo: str, project: str, bot: str, from_bot: str, now: float) -> dict[str, object]:
    directory = requests_dir(home, bootstrap)
    base = f"{_stamp(now)}-{project}"
    target = directory / f"{base}.json"
    index = 2
    while target.exists():
        target = directory / f"{base}-{index}.json"
        index += 1
    record: dict[str, object] = {
        "version": 1, "kind": "project", "id": target.stem, "bootstrap": bootstrap, "repo": repo, "project": project,
        "bot": bot, "from": from_bot, "created_at": now, "created_at_iso": _iso(now),
        "expires_at": now + REQUEST_TTL_SECONDS, "expires_at_iso": _iso(now + REQUEST_TTL_SECONDS),
    }
    _write_record(target, record)
    return {k: record[k] for k in (*REQUEST_FIELDS, "expires_at_iso")}


def project_request(home: Path, bootstrap: str, request_id: str, now: float) -> ProjectRequest:
    if not REQUEST_ID.match(request_id or ""):
        raise RequestError(f"no project request {request_id!r}")
    found = next((r for r in active_requests(home, bootstrap, now) if r.id == request_id), None)
    if found is None:
        raise RequestError(f"no project request {request_id} waiting for {bootstrap}; it was set up already, or it expired")
    return found


def next_unnotified_request(home: Path, bootstrap: str, now: float) -> ProjectRequest | None:
    return next((r for r in active_requests(home, bootstrap, now) if not r.data.get("notified_at")), None)


def mark_request(request: ProjectRequest, now: float, step: str) -> None:
    """Record a step ("notified" or "started") on a request that still exists; one setup-request already removed stays removed."""
    if request.path.is_file():
        _write_record(request.path, {**request.data, f"{step}_at": now, f"{step}_at_iso": _iso(now)})


def finish_request(request: ProjectRequest) -> None:
    _remove(request.path)


def format_request_notice(request: ProjectRequest) -> str:
    return (
        f"team-kit notice: chief project request {request.id} for {request.field('repo')} from {request.field('from')}: "
        f"set up project {request.field('project')} with bot {request.field('bot')}. Expires {_iso(request.expires_at)}."
    )
