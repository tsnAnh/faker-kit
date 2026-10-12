"""Start one cloud coding agent and return the session it created.

A Claude Code session is started in two steps, so no prompt text ever reaches a process argument.
`claude --cloud "<first message>"` creates the session; its argument is a fixed sentence telling the
session that its task comes next. The CLI creates a session only on a terminal, from a directory whose
GitHub remote is the repository, and asks once per directory whether to trust it. So every launch runs
from one kept checkout per repository, which the owner trusts once, on a pseudo-terminal acquired as the
child's controlling terminal (TIOCSCTTY), with `CI` removed from its environment. Then
`claude --cloud <session> -p` sends the real prompt, signing keys included, on stdin; that form needs no
terminal and queues the message into the session.

`codex cloud exec` names its environment, which is bound to a repository, so it runs from an empty
directory with its prompt on stdin (`codex cloud exec -`), on the same kind of pseudo-terminal.

The terminal output has its escape sequences stripped and is read for the session URL. A redacted snapshot
up to the session URL or startup error is written to a log next to the working directory. No shell is
involved. After the session is known the output keeps being drained and the child is reaped in a background
thread, so it may keep running after this returns.
"""

from __future__ import annotations

import fcntl
import os
import re
import select
import shutil
import signal
import subprocess
import termios
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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
# Claude Code launches from one kept checkout per repository, never pruned, which the owner trusts once.
CLAUDE_CHECKOUT = "repo"
CLAUDE_FIRST_MESSAGE = ("Your task arrives as the next message in this session. "
                        "Until it does, change nothing and reply only: waiting for the task.")
CLAUDE_SENT = "Sent to cloud session"
CLAUDE_SESSION_ID = re.compile(r"\A(?:session|cse)_[A-Za-z0-9]+\Z")
# The CLI's trust question; the terminal cleanup can drop the spaces between its words.
TRUST_QUESTION = re.compile(r"trust\s*this\s*folder", re.I)
SEND_TIMEOUT_SECONDS = 120
GIT_TIMEOUT_SECONDS = 120

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


def _reap(proc: subprocess.Popen[bytes], timeout: float | None = None) -> None:
    try:  # noqa: SIM105
        proc.wait(timeout)
    except Exception:  # still running or already gone; nothing more to do here  # noqa: BLE001, S110
        pass


def _drain(fd: int, proc: subprocess.Popen[bytes] | None = None) -> None:
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
        try:  # noqa: SIM105
            os.close(fd)
        except OSError:
            pass
        if proc is not None:
            _reap(proc)


@dataclass
class Cloud:
    run_gh: Runner
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
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
        """Start command for repo and return the session URL or id. A Codex task runs in workdir, emptied first;
        a Claude Code session launches from the repository's kept checkout beside it, and workdir names its log."""
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self._prune(workdir.parent)
        log = workdir.with_name(workdir.name + ".log")
        if backend == "claude":
            checkout = self.claude_checkout(repo, workdir.parent / CLAUDE_CHECKOUT)
            session = self._launch([command.argv[0], "--cloud", CLAUDE_FIRST_MESSAGE], checkout, None, log, command)
            self._send(command, session, checkout)
            return session
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir()
        return self._launch(list(command.argv), workdir, command.prompt.encode(), log, command)

    def claude_checkout(self, repo: str, path: Path) -> Path:
        """The kept checkout Claude Code launches from: cloned once, then moved to the default branch's tip."""
        if not (path / ".git").exists():
            shutil.rmtree(path, ignore_errors=True)
            self.run_gh(["repo", "clone", repo, str(path), "--", "--depth", "1", "--quiet"])
            return path
        for args in (["fetch", "--quiet", "--depth", "1", "origin", "HEAD"], ["reset", "--quiet", "--hard", "FETCH_HEAD"]):
            try:
                done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                raise CloudError(f"git {args[0]} did not finish in {path}") from None
            if done.returncode:
                raise CloudError(f"git {args[0]} failed in {path}: {done.stderr.strip()[:200]}")
        return path

    def _launch(self, argv: list[str], cwd: Path, stdin: bytes | None, log: Path, command: CloudCommand) -> str:
        """Run argv on a pseudo-terminal in cwd until it prints a session, and return it."""
        log.write_text("", encoding="utf-8")
        # This side keeps its own copy of the terminal open until the session is read: macOS drops unread
        # output once every copy is closed, so a child that prints and exits at once would lose its line.
        master, slave = os.openpty()
        keep_master = False
        try:
            proc = self.popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=slave,
                stderr=slave,
                env=_child_env(),
                start_new_session=True,
                preexec_fn=_acquire_terminal,
            )
            if stdin is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin)
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
                    os.close(slave)
                    slave = -1
                    threading.Thread(target=_drain, args=(master, proc), daemon=True).start()
                    return found.group(0)
                if TRUST_QUESTION.search(text):
                    self._stop(proc)
                    self._write_log(log, text, command)
                    raise CloudError(f"Claude Code asks whether to trust {cwd}; trust it once: cd {cwd} && claude, choose yes, then /exit")
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
                    self._stop(proc)
                    raw += _read_available(master, 0.05)
                    self._write_log(log, clean_terminal_text(raw), command)
                    raise CloudError(f"{argv[0]} printed no session in {int(self.wait_seconds)}s; see {log}")
                self.sleep(1.0)
        finally:
            for fd in ([slave] if slave >= 0 else []) + ([] if keep_master else [master]):
                try:  # noqa: SIM105
                    os.close(fd)
                except OSError:
                    pass

    def _send(self, command: CloudCommand, session: str, cwd: Path) -> None:
        """Queue the prompt into the new session on stdin. No terminal is needed, and no argument carries it."""
        session_id = session.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
        if not CLAUDE_SESSION_ID.match(session_id):
            raise CloudError(f"{session} is not a Claude Code session the CLI can message")
        try:
            done = subprocess.run([command.argv[0], "--cloud", session_id, "-p"], cwd=str(cwd), input=command.prompt,
                                  capture_output=True, text=True, env=_child_env(), timeout=SEND_TIMEOUT_SECONDS)
            out = done.stdout + done.stderr
        except (OSError, subprocess.TimeoutExpired) as err:
            done, out = None, type(err).__name__
        if done is None or done.returncode or CLAUDE_SENT not in out:
            last = next((line for line in reversed(self._redact(out, command).splitlines()) if line.strip()), "no output")
            raise CloudError(f"{session} started but did not get its task, so it waits idle: {last[:200]}")

    @staticmethod
    def _stop(proc: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, AttributeError):
            proc.kill()
        _reap(proc, 5)

    @staticmethod
    def _redact(text: str, command: CloudCommand) -> str:
        safe_text = text.replace(command.prompt, REDACTED_PROMPT) if command.prompt else text
        for secret in command.sensitive:
            if secret:
                safe_text = safe_text.replace(secret, REDACTED_KEY)
        return safe_text

    @classmethod
    def _write_log(cls, log: Path, text: str, command: CloudCommand) -> str:
        safe_text = cls._redact(text, command)
        log.write_text(safe_text, encoding="utf-8", errors="replace")
        return safe_text

    def _prune(self, parent: Path) -> None:
        cutoff = self.now() - KEEP_SECONDS
        for entry in parent.iterdir():
            if entry.name == CLAUDE_CHECKOUT:
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            except OSError:
                pass
