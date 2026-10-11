"""Start one cloud coding agent and return the session it created.

A Claude Code agent starts by firing the project's routine: one HTTPS POST to the routine's `/fire`
endpoint with its bearer token in a header and the prompt in the JSON body. The routine's saved prompt
tells the session to do the task in its fire payload; the routine names the repository, environment,
and model. The answer carries the session URL. No local process, clone, or terminal is involved, and
the prompt (which may hold a signing key) and the token never reach argv, a URL, or a log.

`codex cloud exec` names its environment, which is bound to a repository, so it runs from an empty
directory. Its prompt is written to its stdin (`codex cloud exec -`), never to argv. It runs in its own
session with a pseudo-terminal acquired as its controlling terminal (TIOCSCTTY) on stdout and stderr, and
`CI` is removed from its environment. The terminal output has its escape sequences stripped and is read
for the task URL. A redacted snapshot up to the task URL or startup error is written to a log next to the
working directory. No shell is involved. After the task is known the output keeps being drained and the
child is reaped in a background thread, so it may keep running after this returns.
"""

from __future__ import annotations

import os
import re
import fcntl
import json
import select
import shutil
import signal
import subprocess
import termios
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from team_kit_contracts import CloudCommand

SESSION = re.compile(
    r"https://claude\.ai/code/[A-Za-z0-9_-]+"
    r"|https://chatgpt\.com/codex/tasks/[A-Za-z0-9_-]+"
    r"|\b(?:session|cse|task)_[A-Za-z0-9]{6,}\b"
)
ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"                 # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"     # OSC
    r"|\x1b[PX^_][^\x1b]*\x1b\\"              # DCS, SOS, PM, APC
    r"|\x1b[ -/]*[0-~]"                         # two-character escapes such as ESC 7
)
KEEP_SECONDS = 3 * 24 * 3600
# `codex cloud status <TASK_ID>` prints `[PENDING|READY|APPLIED|ERROR] <title>` first (codex-rs
# cloud-tasks) and exits 1 for anything but READY. It has no --json option, and the title is
# free text, so only the bracketed label is read.
STATUS_LABEL = re.compile(r"\A\[(PENDING|READY|APPLIED|ERROR)\]")
TASK_ID = re.compile(r"(?:\A|/codex/tasks/)(task_[A-Za-z0-9_-]+)\Z")
STATUS_TIMEOUT_SECONDS = 30
FINISHED = frozenset({"ready", "applied", "error"})
REDACTED_PROMPT = "[redacted prompt]"
REDACTED_KEY = "[redacted signing key]"
# A routine is fired at its own endpoint with its own token. The beta header is the documented
# version of this research-preview API; a newer one ships under a new date.
ROUTINE_TOKEN_ENV = "TEAM_KIT_ROUTINE_TOKEN"
ROUTINE_ID = re.compile(r"\Atrig_[A-Za-z0-9]{8,64}\Z")
ROUTINE_FIRE_URL = "https://api.anthropic.com/v1/claude_code/routines/{routine}/fire"
ROUTINE_BETA = "experimental-cc-routine-2026-04-01"
ROUTINE_FIRE_TIMEOUT_SECONDS = 30
CLAUDE_SESSION_URL = re.compile(r"\Ahttps://claude\.ai/code/[A-Za-z0-9_-]+\Z")
API_REASON = re.compile(r"\A[a-z0-9_]{1,64}\Z")
FIRE_HINTS = {
    401: "the routine token is wrong or was revoked; generate a new one on the routine's page",
    403: "the routine token cannot fire this routine",
    404: "no such routine; check routine: in bots.yaml",
    429: "the routine's fire limit is reached (30 an hour); try again later",
}
REASON_HINTS = {
    "routine_paused": "the routine is paused; turn it on at claude.ai/code/routines",
}

Runner = Callable[..., str]
StatusRunner = Callable[[list[str]], str]
Poster = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def run_codex_status(argv: list[str]) -> str:
    """Standard output of a `codex cloud status` call. Its exit code carries nothing the label does not."""
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=STATUS_TIMEOUT_SECONDS, check=False)
    return done.stdout


class CloudError(Exception):
    pass


@dataclass(frozen=True)
class Routine:
    """A Claude Code routine whose saved prompt does the task in its fire payload, and the token that fires it."""
    id: str
    token: str = field(repr=False)


def owned_routine(routine_id: str | None, owner_repo: str | None, token: str) -> Callable[[str], Routine | None]:
    """The routine resolver for one project: its routine starts sessions for its own repository and no other."""
    def resolve(repo: str) -> Routine | None:
        if routine_id and owner_repo and repo.lower() == owner_repo.lower():
            return Routine(routine_id, token)
        return None
    return resolve


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the bearer token to another URL, so none is followed."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post_json(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, bytes]:
    """POST body to url and return the status and at most 64 KiB of the answer. Network errors raise OSError."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read(65536)
    except urllib.error.HTTPError as err:
        return err.code, err.read(65536)


def fire_routine(routine: Routine, text: str, post: Poster = post_json,
                 timeout: float = ROUTINE_FIRE_TIMEOUT_SECONDS) -> str:
    """Fire routine with text as its payload and return the session URL. Never retries: a fire that timed
    out may still have started a session, and a second one would do the work twice."""
    if not ROUTINE_ID.match(routine.id or ""):
        raise CloudError("the routine id must look like trig_ followed by letters and digits")
    if not routine.token:
        raise CloudError(f"no routine token: put it in the project bot profile's .env as {ROUTINE_TOKEN_ENV}")
    headers = {
        "Authorization": f"Bearer {routine.token}",
        "anthropic-beta": ROUTINE_BETA,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        status, raw = post(ROUTINE_FIRE_URL.format(routine=routine.id), headers, json.dumps({"text": text}).encode(), timeout)
    except (OSError, ValueError) as err:  # URLError, timeouts, and TLS failures; their text may quote the request
        raise CloudError(f"firing routine {routine.id} did not reach the API ({type(err).__name__}); try again later") from None
    try:
        answer = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        answer = None
    if status != 200:
        # Only the API's reason code is kept: its message is free text this module does not need to repeat.
        error = answer.get("error") if isinstance(answer, dict) else None
        reason = error.get("reason") if isinstance(error, dict) else None
        reason = reason if isinstance(reason, str) and API_REASON.match(reason) else None
        hint = REASON_HINTS.get(reason or "") or FIRE_HINTS.get(status)
        raise CloudError(f"routine {routine.id} refused the fire: HTTP {status}{' ' + reason if reason else ''}"
                         f"{'; ' + hint if hint else ''}")
    url = answer.get("claude_code_session_url") if isinstance(answer, dict) else None
    if not isinstance(url, str) or not CLAUDE_SESSION_URL.match(url):
        raise CloudError(f"routine {routine.id} answered the fire without a session URL")
    return url


def clean_terminal_text(raw: bytes) -> str:
    """Terminal output as plain text: escape sequences removed, line endings normalized."""
    text = ANSI.sub("", raw.decode(errors="replace"))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_available(fd: int, timeout: float) -> bytes:
    """Whatever the pseudo-terminal has now, waiting up to timeout for the first byte."""
    data = b""
    try:
        ready, _, _ = select.select([fd], [], [], timeout)
        while ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
            ready, _, _ = select.select([fd], [], [], 0)
    except OSError:  # EIO: every writer has closed the terminal
        pass
    return data


def _acquire_terminal() -> None:
    """Runs in the child after setsid: make the pseudo-terminal on stdout its controlling terminal."""
    fcntl.ioctl(1, termios.TIOCSCTTY, 0)


def _child_env() -> dict[str, str]:
    """The environment the agent inherits, minus CI, which makes the CLI refuse the terminal."""
    return {key: value for key, value in os.environ.items() if key != "CI"}


def _reap(proc, timeout: float | None = None) -> None:
    try:
        proc.wait(timeout)
    except Exception:  # still running or already gone; nothing more to do here
        pass


def _drain(fd: int, proc=None) -> None:
    """Keep reading after the session is known, so a running agent never blocks on a full terminal,
    then reap it once it exits."""
    try:
        while True:
            select.select([fd], [], [])
            if not os.read(fd, 65536):
                break
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        if proc is not None:
            _reap(proc)


@dataclass
class Cloud:
    popen: Callable = subprocess.Popen
    wait_seconds: float = 180.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], float] = field(default=time.time)
    run_status: StatusRunner = run_codex_status
    # The routine that starts Claude Code sessions for a repository, or None when it has none.
    routines: Callable[[str], Routine | None] = field(default=lambda repo: None)
    post: Poster = post_json

    def task_status(self, backend: str, session: str) -> str:
        """pending, ready, applied, or error for a Codex cloud task; unknown for anything else.

        Unknown covers a Claude session, a session that is not a Codex task, a missing or signed-out
        codex binary, a timeout, and output with no label. Callers then keep their one-day fallback.
        """
        found = TASK_ID.search(str(session).strip().split("?")[0].split("#")[0]) if backend == "codex" else None
        if not found:
            return "unknown"
        try:
            out = self.run_status(["codex", "cloud", "status", found.group(1)])
        except Exception:  # noqa: BLE001 - nothing it says may reach a notice, a log, or an error message
            return "unknown"
        first = next((line.strip() for line in ANSI.sub("", out or "").splitlines() if line.strip()), "")
        label = STATUS_LABEL.match(first)
        return label.group(1).lower() if label else "unknown"

    def start(self, backend: str, repo: str, command: CloudCommand, workdir: Path) -> str:
        """Start command for repo. Returns the session URL or id. A Claude Code session is a routine fire;
        a Codex task runs with workdir as its directory."""
        if backend == "claude":
            routine = self.routines(repo)
            if routine is None:
                raise CloudError(f"{repo} has no Claude Code routine: add routine: trig_... to its project bot in bots.yaml "
                                 f"(team-kit-admin add-project --routine) and {ROUTINE_TOKEN_ENV} to that bot profile's .env")
            return fire_routine(routine, command.prompt, self.post)
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self._prune(workdir.parent)
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir()
        argv = list(command.argv)
        log = workdir.with_name(workdir.name + ".log")
        log.write_text("", encoding="utf-8")
        master, slave = os.openpty()
        keep_master = False
        try:
            try:
                proc = self.popen(
                    argv,
                    cwd=str(workdir),
                    stdin=subprocess.PIPE,
                    stdout=slave,
                    stderr=slave,
                    env=_child_env(),
                    start_new_session=True,
                    preexec_fn=_acquire_terminal,
                )
            finally:
                os.close(slave)
            try:
                proc.stdin.write(command.prompt.encode())
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            raw = b""
            deadline = self.clock() + self.wait_seconds
            while True:
                raw += _read_available(master, 0.05)
                text = clean_terminal_text(raw)
                found = SESSION.search(text)
                if found:
                    self._write_log(log, text, command)
                    keep_master = True
                    threading.Thread(target=_drain, args=(master, proc), daemon=True).start()
                    return found.group(0)
                code = proc.poll()
                if code is not None:
                    raw += _read_available(master, 0.05)
                    text = clean_terminal_text(raw)
                    found = SESSION.search(text)
                    if found:
                        self._write_log(log, text, command)
                        return found.group(0)
                    safe_text = self._write_log(log, text, command)
                    last = next((line for line in reversed(safe_text.splitlines()) if line.strip()), "no output")
                    raise CloudError(f"{argv[0]} exited {code} before starting a session: {last[:200]}")
                if self.clock() >= deadline:
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except (OSError, AttributeError):
                        proc.kill()
                    _reap(proc, 5)
                    raw += _read_available(master, 0.05)
                    self._write_log(log, clean_terminal_text(raw), command)
                    raise CloudError(f"{argv[0]} printed no session in {int(self.wait_seconds)}s; see {log}")
                self.sleep(1.0)
        finally:
            if not keep_master:
                try:
                    os.close(master)
                except OSError:
                    pass

    @staticmethod
    def _write_log(log: Path, text: str, command: CloudCommand) -> str:
        safe_text = text.replace(command.prompt, REDACTED_PROMPT) if command.prompt else text
        for secret in command.sensitive:
            if secret:
                safe_text = safe_text.replace(secret, REDACTED_KEY)
        log.write_text(safe_text, encoding="utf-8", errors="replace")
        return safe_text

    def _prune(self, parent: Path) -> None:
        cutoff = self.now() - KEEP_SECONDS
        for entry in parent.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            except OSError:
                pass
