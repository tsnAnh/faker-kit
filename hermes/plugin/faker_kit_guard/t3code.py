from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

PREFIX = "mcp_t3code_"
LAUNCH_TOOL = f"{PREFIX}t3_thread_launch"
SEND_TOOL = f"{PREFIX}t3_thread_send"
LAUNCHES = "launches"
LAUNCH_TTL_SECONDS = 3600
RECORD_KEEP_SECONDS = 7 * 24 * 3600
RECORD_VERSION = 1
LOCK_NAME = ".lock"
DENIED_REASONS = {
    "t3_thread_merge_back": "merging a thread back changes another thread's work, and landing is the owner's",
    "t3_thread_transfer": "forking or merging a thread starts or changes work outside faker-kit launch",
    "t3_project_create": "registering a folder on the owner's Mac is the owner's",
    "t3_project_clone": "cloning onto the owner's Mac is the owner's",
    "t3_project_update": "a project's settings and saved scripts are the owner's",
    "t3_project_delete": "deleting a project is the owner's",
    "t3_worktree_handoff": "handing a thread to a worktree starts work outside faker-kit launch",
    "t3_environment_link": "linking another machine is the owner's",
    "t3_environment_unlink": "unlinking another machine is the owner's",
    "t3_thread_configure": "a thread's model comes from the repository's dispatch.json, not the bot",
    "t3_thread_send_attachments": "files go to a thread only through the owner",
    "t3_attachment_prepare_upload": "files go to a thread only through the owner",
    "t3_attachment_discard": "files go to a thread only through the owner",
    "create_threads": "a thread starts only through faker-kit launch and t3_thread_launch",
    "delegate_task": "a thread starts only through faker-kit launch and t3_thread_launch",
    "request_secret": "a bot never asks for or handles a secret",
}
READERS = frozenset({
    "t3_thread_read", "t3_thread_wait", "t3_thread_list", "t3_thread_search", "t3_pending_request", "t3_worktree_status", "t3_worktree_list",
})
SECRET_SHAPES = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"
    r"|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def message_digest(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()


def launches_dir(home: Path, bot: str) -> Path:
    return home / LAUNCHES / bot


def record_path(home: Path, bot: str, digest: str) -> Path:
    return launches_dir(home, bot) / f"{digest}.json"


@contextlib.contextmanager
def locked(directory: Path) -> Iterator[None]:
    fd = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def write_private(path: Path, data: dict[str, object]) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(json.dumps(data, sort_keys=True))
            out.flush()
            os.fsync(out.fileno())
        temp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise


def read_record(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def strings_of(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in strings_of(v)]
    if isinstance(value, (list, tuple)):
        return [t for v in value for t in strings_of(v)]
    return []


def denied(tool_name: str, args: dict[str, object]) -> str | None:
    if not tool_name.startswith(PREFIX):
        return None
    short = tool_name.removeprefix(PREFIX)
    if short in DENIED_REASONS:
        return DENIED_REASONS[short]
    if short == "t3_pending_request" and args.get("action") == "respond":
        return "answering a thread's question is the owner's: ask with clarify, then steer with t3_thread_send"
    if tool_name == SEND_TOOL and any(SECRET_SHAPES.search(text) for text in strings_of(args)):
        return "the message looks like it holds a token or key; never send a secret to a thread"
    return None


def reader(tool_name: str) -> bool:
    return tool_name.startswith(PREFIX) and tool_name.removeprefix(PREFIX) in READERS


def launch_scan_args(args: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in args.items() if key != "message"}


def prepared_problem(record: dict[str, object] | None, digest: str, bot: str, args: dict[str, object], now: float) -> str | None:
    if record is None or record.get("version") != RECORD_VERSION or record.get("prompt_sha256") != digest or record.get("bot") != bot:
        return "no prepared launch matches this message: run faker-kit launch --brief-file on its brief and send the printed call unchanged"
    if record.get("used") is not False:
        return "this prepared launch was already used: a second thread needs its own faker-kit launch"
    created = record.get("created")
    if isinstance(created, bool) or not isinstance(created, (int, float)) or not 0 <= now - created <= LAUNCH_TTL_SECONDS:
        return f"this prepared launch is older than {LAUNCH_TTL_SECONDS // 60} minutes: run faker-kit launch again"
    wanted = record.get("call")
    if not isinstance(wanted, dict):
        return "the prepared launch holds no call"
    given = launch_scan_args(args)
    if record.get("project_is_path") is True:
        project = given.pop("projectId", None)
        if not isinstance(project, str) or not project:
            return "projectId is the id of the project that t3_project_list shows for the prepared workspace path"
    if given != wanted:
        names = sorted(key for key in {*given, *wanted} if given.get(key) != wanted.get(key))
        return f"the call differs from the prepared launch in {', '.join(names)}: send exactly what faker-kit launch printed"
    return None


def launch_problem(home: Path, bot: str, args: dict[str, object], now: float) -> str | None:
    message = args.get("message")
    if not isinstance(message, str) or not message:
        return "t3_thread_launch needs the message faker-kit launch printed"
    digest = message_digest(message)
    path = record_path(home, bot, digest)
    if not path.parent.is_dir():
        return prepared_problem(None, digest, bot, args, now)
    try:
        with locked(path.parent):
            record = read_record(path)
            problem = prepared_problem(record, digest, bot, args, now)
            if problem is None and record is not None:
                write_private(path, {**record, "used": True, "used_at": now})
    except OSError:
        return "the prepared launch could not be recorded as used; nothing starts"
    return problem
