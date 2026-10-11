"""Start one cloud coding agent and return the session it created.

`claude --cloud` clones the GitHub remote of the directory it runs in, so a Claude Code launch
runs from a fresh shallow clone of the unit's own repository. `codex cloud exec` names its
environment, which is bound to a repository, so it runs from an empty directory.

Claude Code refuses to start a new cloud session unless it can read the keyboard: stdin is a
terminal, or else it opens `/dev/tty` (the CLI's terminal-input check, which fails with "No cloud
session was created or opened: it would open in this terminal and need the keyboard"). A bare
`claude --cloud` takes its description from piped stdin, so stdin stays a pipe and the pseudo-terminal
must be the child's controlling terminal for `/dev/tty` to open. Every agent therefore runs in its own
session with the pseudo-terminal slave acquired as its controlling terminal (TIOCSCTTY) and on stdout
and stderr. The prompt is written to the agent's stdin (`codex cloud exec -` likewise), never to
argv. The CLI also gives up the terminal when `CI` is set, so `CI` is removed from the child's
environment. The terminal output has its escape sequences stripped and is read for the session URL.
A redacted snapshot up to the session URL or startup error is written to a log next to the working
directory. No shell is involved. After the session is known the output keeps being drained and the
child is reaped in a background thread, so it may keep running after this returns.
"""

from __future__ import annotations

import os
import re
import fcntl
import select
import shutil
import signal
import subprocess
import termios
import threading
import time
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

Runner = Callable[..., str]
StatusRunner = Callable[[list[str]], str]


def run_codex_status(argv: list[str]) -> str:
    """Standard output of a `codex cloud status` call. Its exit code carries nothing the label does not."""
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=STATUS_TIMEOUT_SECONDS, check=False)
    return done.stdout


class CloudError(Exception):
    pass


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
    run_gh: Runner
    popen: Callable = subprocess.Popen
    wait_seconds: float = 180.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], float] = field(default=time.time)
    run_status: StatusRunner = run_codex_status

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
        """Run command for repo with workdir as its directory. Returns the session URL or id."""
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self._prune(workdir.parent)
        if workdir.exists():
            shutil.rmtree(workdir)
        if backend == "claude":
            self.run_gh(["repo", "clone", repo, str(workdir), "--", "--depth", "1", "--quiet"])
        else:
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
