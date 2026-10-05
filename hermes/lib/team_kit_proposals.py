"""Chief proposals passed to project bots through local files."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

DAY = 24 * 3600
PROPOSAL_TTL_SECONDS = 7 * DAY
MAX_PROPOSAL_BYTES = 60_000
SOURCE_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md\Z")
SAFE_STEM = re.compile(r"[^A-Za-z0-9_.-]+")


class ProposalError(ValueError):
    pass


@dataclass(frozen=True)
class Proposal:
    path: Path
    data: dict

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
        return float(self.data["expires_at"])


def proposals_dir(home: Path, bot: str) -> Path:
    return home / "proposals" / bot


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _stamp(seconds: float) -> str:
    return time.strftime("%Y%m%dt%H%M%Sz", time.gmtime(seconds))


def _source(path: str) -> tuple[Path, str]:
    target = Path(os.path.expanduser(path)).resolve()
    name = target.name
    if not SOURCE_NAME.match(name):
        raise ProposalError("--file must name a plain .md file whose basename starts with a letter or digit")
    try:
        stat = target.stat()
    except OSError:
        raise ProposalError(f"cannot read {target}") from None
    if not target.is_file():
        raise ProposalError(f"{target} is not a regular file")
    if stat.st_size <= 0:
        raise ProposalError(f"{target} is empty")
    if stat.st_size > MAX_PROPOSAL_BYTES:
        raise ProposalError(f"{target} is larger than {MAX_PROPOSAL_BYTES} bytes")
    try:
        text = target.read_text()
    except UnicodeDecodeError:
        raise ProposalError(f"{target} is not UTF-8 text") from None
    except OSError:
        raise ProposalError(f"cannot read {target}") from None
    if not text.strip():
        raise ProposalError(f"{target} is empty")
    return target, text


def write_proposal(home: Path, bot: str, project: str, repo: str, source_file: str, from_bot: str, now: float) -> dict:
    source, body = _source(source_file)
    directory = proposals_dir(home, bot)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    stem = SAFE_STEM.sub("-", source.stem).strip(".-")[:48] or "proposal"
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
        "source_name": source.name,
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
    keys = ("id", "project", "bot", "repo", "from", "source_name", "created_at_iso", "expires_at_iso")
    return {k: record[k] for k in keys} | {"path": str(target)}


def _read(path: Path) -> Proposal | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    required = {"version", "id", "project", "bot", "repo", "from", "created_at", "expires_at", "body"}
    if not isinstance(data, dict) or data.get("version") != 1 or not required <= set(data):
        return None
    try:
        float(data["created_at"])
        float(data["expires_at"])
    except (TypeError, ValueError):
        return None
    return Proposal(path, data)


def _expired(proposal: Proposal, now: float) -> bool:
    return now >= proposal.expires_at


def active_proposals(home: Path, bot: str, now: float, prune: bool = True) -> list[Proposal]:
    directory = proposals_dir(home, bot)
    if not directory.is_dir():
        return []
    active: list[Proposal] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name):
        proposal = _read(path)
        if proposal is None:
            continue
        if _expired(proposal, now):
            if prune:
                path.unlink(missing_ok=True)
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
