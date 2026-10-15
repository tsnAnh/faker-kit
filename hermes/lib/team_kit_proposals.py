"""Chief proposals passed to project bots through local files."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DAY = 24 * 3600
PROPOSAL_TTL_SECONDS = 7 * DAY
MAX_PROPOSAL_BYTES = 20_000
# A record is the draft plus a little JSON, so anything bigger than this is not one of ours.
MAX_RECORD_BYTES = 2 * MAX_PROPOSAL_BYTES * 4 + 4096
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
SOURCE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md\Z")
SAFE_STEM = re.compile(r"[^A-Za-z0-9_.-]+")


class ProposalError(ValueError):
    pass


@dataclass(frozen=True)
class Proposal:
    path: Path
    data: dict[str, object]

    @property
    def id(self) -> str:
        return str(self.data["id"])

    @property
    def title(self) -> str:
        for line in str(self.data.get("body") or "").splitlines():
            cleaned = " ".join(line.lstrip("# ").split())
            if cleaned:
                return cleaned[:100]
        return self.id

    @property
    def expires_at(self) -> float:
        expires = self.data["expires_at"]
        return float(expires) if isinstance(expires, (int, float, str)) else 0.0


def proposals_dir(home: Path, bot: str) -> Path:
    return home / "proposals" / bot


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _stamp(seconds: float) -> str:
    return time.strftime("%Y%m%dt%H%M%Sz", time.gmtime(seconds))


def drafts_dir(home: Path, bot: str) -> Path:
    """The only place the chief may write a draft, and the only place `propose` reads one from."""
    return home / "drafts" / bot


def _source(home: Path, from_bot: str, path: str) -> tuple[str, str, str]:
    """(name, text, sha256) of one draft, read once so the hash and the stored body are the same bytes."""
    directory = drafts_dir(home, from_bot)
    target = os.path.abspath(os.path.expanduser(path))  # noqa: PTH100, PTH111
    if os.path.dirname(target) != os.path.abspath(directory):  # noqa: PTH100, PTH120
        raise ProposalError(f"--file must be a file directly in {directory}")
    name = os.path.basename(target)  # noqa: PTH119
    if not SOURCE_NAME.match(name):
        raise ProposalError("--file must name a plain .md file whose basename starts with a letter or digit")
    try:
        if directory.is_symlink() or directory.parent.is_symlink():
            raise ProposalError(f"{directory} must not be a symlink")  # noqa: TRY301
        fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except ProposalError:
        raise
    except OSError:
        raise ProposalError(f"cannot read {name} in {directory}; it must be a regular file, not a symlink") from None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ProposalError(f"{name} is not a regular file")
        if info.st_size <= 0:
            raise ProposalError(f"{name} is empty")
        if info.st_size > MAX_PROPOSAL_BYTES:
            raise ProposalError(f"{name} is larger than {MAX_PROPOSAL_BYTES} bytes")
        raw = handle.read(MAX_PROPOSAL_BYTES + 1)
    if len(raw) > MAX_PROPOSAL_BYTES:
        raise ProposalError(f"{name} is larger than {MAX_PROPOSAL_BYTES} bytes")
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        raise ProposalError(f"{name} is not UTF-8 text") from None
    if not text.strip():
        raise ProposalError(f"{name} is empty")
    return name, text, hashlib.sha256(raw).hexdigest()


def file_digest(home: Path, from_bot: str, path: str) -> str:
    """What `propose --sha256` must equal. Same checks as propose, so a bad draft fails here first."""
    return _source(home, from_bot, path)[2]


def write_proposal(home: Path, bot: str, project: str, repo: str, source_file: str, from_bot: str, now: float, sha256: str) -> dict[str, object]:
    if not SHA256.match(sha256 or ""):
        raise ProposalError("--sha256 must be the 64 lowercase hex characters of the file's SHA-256 (team-kit-chief digest --file F)")
    name, body, actual = _source(home, from_bot, source_file)
    if actual != sha256:
        raise ProposalError(f"--sha256 does not match {name} as it is now; run digest again and ask the owner to approve the new command")
    directory = proposals_dir(home, bot)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    stem = SAFE_STEM.sub("-", name.removesuffix(".md")).strip(".-")[:48] or "proposal"
    base = f"{_stamp(now)}-{stem}"
    target = directory / f"{base}.json"
    index = 2
    while target.exists():
        target = directory / f"{base}-{index}.json"
        index += 1
    proposal_id = target.stem
    record = {
        "version": 1,
        "id": proposal_id,
        "project": project,
        "bot": bot,
        "repo": repo,
        "from": from_bot,
        "source_name": name,
        "sha256": actual,
        "created_at": now,
        "created_at_iso": _iso(now),
        "expires_at": now + PROPOSAL_TTL_SECONDS,
        "expires_at_iso": _iso(now + PROPOSAL_TTL_SECONDS),
        "body": body.rstrip() + "\n",
    }
    tmp = target.with_name(f".{target.name}.{os.getpid()}")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True))
    tmp.replace(target)
    keys = ("id", "project", "bot", "repo", "from", "source_name", "sha256", "created_at_iso", "expires_at_iso")
    return {k: record[k] for k in keys} | {"path": str(target)}


def _read(path: Path, bot: str) -> Proposal | None:
    """The record, or None if it is corrupt: unreadable, oversized, the wrong shape, or not this bot's."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
            return None
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    required = {"version", "id", "project", "bot", "repo", "from", "created_at", "expires_at", "body"}
    if not isinstance(data, dict) or data.get("version") != 1 or not required <= set(data):
        return None
    if not all(isinstance(data[k], str) for k in ("id", "project", "bot", "repo", "from", "body")):
        return None
    if data["id"] != path.stem or data["bot"] != bot:
        return None
    try:
        created, expires = float(data["created_at"]), float(data["expires_at"])
    except (TypeError, ValueError):
        return None
    if not (created < expires <= created + PROPOSAL_TTL_SECONDS) or not data["body"].strip():
        return None
    return Proposal(path, data)


def _expired(proposal: Proposal, now: float) -> bool:
    return now >= proposal.expires_at


def _remove(path: Path) -> None:
    """Delete one bad or expired entry of our own private folder; never let one stuck entry hide the rest."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def active_proposals(home: Path, bot: str, now: float, prune: bool = True) -> list[Proposal]:
    """Unexpired records, oldest first. With prune, expired and corrupt records are deleted on the way."""
    directory = proposals_dir(home, bot)
    if not directory.is_dir():
        return []
    active: list[Proposal] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name):
        proposal = _read(path, bot)
        if proposal is None or _expired(proposal, now):
            if prune:
                _remove(path)
            continue
        active.append(proposal)
    return active


def format_notice(proposal: Proposal) -> str:
    expires = str(proposal.data.get("expires_at_iso") or _iso(proposal.expires_at))
    return (
        f"team-kit notice: chief proposal {proposal.id} for {proposal.data['project']} from {proposal.data['from']}: "
        f"{proposal.title}. Ask me in this Bot Chat to brief it if you want this project to do it. Expires {expires}."
    )


def format_listing(proposals: list[Proposal]) -> list[str]:
    if not proposals:
        return ["[SILENT]"]
    lines: list[str] = []
    for proposal in proposals:
        expires = str(proposal.data.get("expires_at_iso") or _iso(proposal.expires_at))
        lines.append(
            f"team-kit notice: chief proposal {proposal.id} from {proposal.data['from']} "
            f"for {proposal.data['project']} expires {expires}"
        )
        lines.append(str(proposal.data["body"]).rstrip())
    return lines


def next_unnotified(home: Path, bot: str, now: float) -> Proposal | None:
    return next((p for p in active_proposals(home, bot, now) if not p.data.get("notified_at")), None)


def mark_notified(proposal: Proposal, now: float) -> None:
    data = {**proposal.data, "notified_at": now, "notified_at_iso": _iso(now)}
    tmp = proposal.path.with_name(f".{proposal.path.name}.{os.getpid()}")
    tmp.touch(mode=0o600)
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
    tmp.replace(proposal.path)


# Project requests: the chief asks the bootstrap bot to set up a new project. A request is structured (a repository and
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
    if not created < expires <= created + PROPOSAL_TTL_SECONDS:
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
        raise ProposalError(f"--repo {repo!r} must be OWNER/REPO")
    for value, what in ((project, "--project"), (bot, "--bot")):
        if not REQUEST_NAME.match(value or "") or value == "default":
            raise ProposalError(f"{what} {value!r} must be lowercase letters, digits, - and _, starting with a letter or digit, and not default")
    if not REQUEST_NAME.match(from_bot or ""):
        raise ProposalError(f"{from_bot!r} is not a bot name")
    with request_lock(home, bootstrap):
        for request in active_requests(home, bootstrap, now):
            for key, value in (("repo", repo.lower()), ("project", project), ("bot", bot)):
                if request.field(key).lower() == value:
                    raise ProposalError(f"request {request.id} waiting for {bootstrap} already names {key} {request.field(key)}")
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
        "expires_at": now + PROPOSAL_TTL_SECONDS, "expires_at_iso": _iso(now + PROPOSAL_TTL_SECONDS),
    }
    _write_record(target, record)
    return {k: record[k] for k in (*REQUEST_FIELDS, "expires_at_iso")}


def project_request(home: Path, bootstrap: str, request_id: str, now: float) -> ProjectRequest:
    if not REQUEST_ID.match(request_id or ""):
        raise ProposalError(f"no project request {request_id!r}")
    found = next((r for r in active_requests(home, bootstrap, now) if r.id == request_id), None)
    if found is None:
        raise ProposalError(f"no project request {request_id} waiting for {bootstrap}; it was set up already, or it expired")
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
