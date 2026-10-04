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

Runner = Callable[..., str]


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
