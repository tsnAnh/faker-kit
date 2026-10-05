"""Start one cloud coding agent and return the session it created.

`claude --cloud` clones the GitHub remote of the directory it runs in, so a Claude Code launch
runs from a fresh shallow clone of the unit's own repository. `codex cloud exec` names its
environment, which is bound to a repository, so it runs from an empty directory.

Claude Code refuses to start a new cloud session unless stdout is a terminal, so every agent runs
with stdout and stderr on a pseudo-terminal. The prompt is written to the agent's stdin (`claude
--cloud` with no value reads it there, `codex cloud exec -` likewise), never to argv. The terminal
output has its escape sequences stripped and is read for the session URL. A redacted snapshot up to
the session URL or startup error is written to a log next to the working directory. No shell is
involved. The process is started in its own session and may keep running after this returns.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
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
REDACTED_PROMPT = "[redacted prompt]"
REDACTED_KEY = "[redacted signing key]"

Runner = Callable[..., str]


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


def _drain(fd: int) -> None:
    """Keep reading after the session is known, so a running agent never blocks on a full terminal."""
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


@dataclass
class Cloud:
    run_gh: Runner
    popen: Callable = subprocess.Popen
    wait_seconds: float = 180.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], float] = field(default=time.time)

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
                    start_new_session=True,
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
                    threading.Thread(target=_drain, args=(master,), daemon=True).start()
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
