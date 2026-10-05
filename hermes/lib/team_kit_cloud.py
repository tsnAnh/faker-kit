"""Start one cloud coding agent and return the session it created.

`claude --cloud` clones the GitHub remote of the directory it runs in, so a Claude Code launch
runs from a fresh shallow clone of the unit's own repository. `codex cloud exec` names its
environment, which is bound to a repository, so it runs from an empty directory.

The process is started in its own session, with its output in a log file next to its working
directory, and keeps running after this returns. The session URL or id is read from that log.
No shell is involved: the command is an argument list.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SESSION = re.compile(
    r"https://claude\.ai/code/[A-Za-z0-9_-]+"
    r"|https://chatgpt\.com/codex/tasks/[A-Za-z0-9_-]+"
    r"|\b(?:session|cse|task)_[A-Za-z0-9]{6,}\b"
)
KEEP_SECONDS = 3 * 24 * 3600
# `codex cloud status <TASK_ID>` prints `[PENDING|READY|APPLIED|ERROR] <title>` first (codex-rs
# cloud-tasks) and exits 1 for anything but READY. It has no --json option, and the title is
# free text, so only the bracketed label is read.
STATUS_LABEL = re.compile(r"\A\[(PENDING|READY|APPLIED|ERROR)\]")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TASK_ID = re.compile(r"(?:\A|/codex/tasks/)(task_[A-Za-z0-9_-]+)\Z")
STATUS_TIMEOUT_SECONDS = 30
FINISHED = frozenset({"ready", "applied", "error"})

Runner = Callable[..., str]
StatusRunner = Callable[[list[str]], str]


def run_codex_status(argv: list[str]) -> str:
    """Standard output of a `codex cloud status` call. Its exit code carries nothing the label does not."""
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=STATUS_TIMEOUT_SECONDS, check=False)
    return done.stdout


class CloudError(Exception):
    pass


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

    def start(self, backend: str, repo: str, argv: list[str], workdir: Path) -> str:
        """Run argv for repo with workdir as its directory. Returns the session URL or id."""
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self._prune(workdir.parent)
        if workdir.exists():
            shutil.rmtree(workdir)
        if backend == "claude":
            self.run_gh(["repo", "clone", repo, str(workdir), "--", "--depth", "1", "--quiet"])
        else:
            workdir.mkdir()
        log = workdir.with_name(workdir.name + ".log")
        with log.open("w") as out:
            proc = self.popen(argv, cwd=str(workdir), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = self.clock() + self.wait_seconds
        while True:
            text = log.read_text(errors="replace")
            found = SESSION.search(text)
            if found:
                return found.group(0)
            code = proc.poll()
            if code is not None:
                text = log.read_text(errors="replace")
                found = SESSION.search(text)
                if found:
                    return found.group(0)
                last = next((line for line in reversed(text.splitlines()) if line.strip()), "no output")
                raise CloudError(f"{argv[0]} exited {code} before starting a session: {last[:200]}")
            if self.clock() >= deadline:
                # Never leave a process behind that might still start a second session for the unit.
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (OSError, AttributeError):
                    proc.kill()
                raise CloudError(f"{argv[0]} printed no session in {int(self.wait_seconds)}s; see {log}")
            self.sleep(1.0)

    def _prune(self, parent: Path) -> None:
        cutoff = self.now() - KEEP_SECONDS
        for entry in parent.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            except OSError:
                pass
