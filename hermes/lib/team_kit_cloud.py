"""Start one cloud coding agent and return the session it created.

`claude --cloud` clones the GitHub remote of the directory it runs in, so a Claude Code launch
runs from a fresh shallow clone of the unit's own repository. `codex cloud exec` names its
environment, which is bound to a repository, so it runs from an empty directory.

The process is started in its own session and may keep running after this returns. Its output is
captured outside named files; a redacted snapshot up to the session URL or startup error is written
to a log next to the working directory. No shell is involved, and prompt text is passed outside argv.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from team_kit_contracts import CloudCommand, PROMPT_FILE, PROMPT_STDIN

SESSION = re.compile(
    r"https://claude\.ai/code/[A-Za-z0-9_-]+"
    r"|https://chatgpt\.com/codex/tasks/[A-Za-z0-9_-]+"
    r"|\b(?:session|cse|task)_[A-Za-z0-9]{6,}\b"
)
KEEP_SECONDS = 3 * 24 * 3600
REDACTED_PROMPT = "[redacted prompt]"
REDACTED_KEY = "[redacted signing key]"

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
        argv, stdin_text, prompt_file = self._prepare(command, workdir)
        log = workdir.with_name(workdir.name + ".log")
        try:
            log.write_text("", encoding="utf-8")
            # The backend writes to an unnamed file. Only a final redacted snapshot reaches the log.
            with tempfile.TemporaryFile(mode="w+b") as capture:
                stdin = subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL
                proc = self.popen(
                    argv,
                    cwd=str(workdir),
                    stdin=stdin,
                    stdout=capture,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                if stdin_text is not None and proc.stdin is not None:
                    try:
                        proc.stdin.write(stdin_text.encode())
                        proc.stdin.close()
                    except (BrokenPipeError, OSError):
                        pass
                deadline = self.clock() + self.wait_seconds
                while True:
                    text = self._capture_text(capture)
                    found = SESSION.search(text)
                    if found:
                        self._write_log(log, text, command.prompt, command.sensitive)
                        return found.group(0)
                    code = proc.poll()
                    if code is not None:
                        text = self._capture_text(capture)
                        found = SESSION.search(text)
                        if found:
                            self._write_log(log, text, command.prompt, command.sensitive)
                            return found.group(0)
                        safe_text = self._write_log(log, text, command.prompt, command.sensitive)
                        last = next((line for line in reversed(safe_text.splitlines()) if line.strip()), "no output")
                        raise CloudError(f"{argv[0]} exited {code} before starting a session: {last[:200]}")
                    if self.clock() >= deadline:
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except (OSError, AttributeError):
                            proc.kill()
                        text = self._capture_text(capture)
                        self._write_log(log, text, command.prompt, command.sensitive)
                        raise CloudError(f"{argv[0]} printed no session in {int(self.wait_seconds)}s; see {log}")
                    self.sleep(1.0)
        finally:
            if prompt_file is not None:
                try:
                    prompt_file.unlink()
                except FileNotFoundError:
                    pass

    def _prepare(self, command: CloudCommand, workdir: Path) -> tuple[list[str], str | None, Path | None]:
        argv = list(command.argv)
        if command.prompt_transport == PROMPT_STDIN:
            return argv, command.prompt, None
        if command.prompt_transport == PROMPT_FILE:
            prompt_file = workdir.with_name(workdir.name + ".prompt.md")
            prompt_file.unlink(missing_ok=True)
            fd = os.open(prompt_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(command.prompt)
            return [*argv, f"@{prompt_file}"], None, prompt_file
        raise CloudError(f"unknown prompt transport {command.prompt_transport!r}")

    @staticmethod
    def _capture_text(capture) -> str:
        size = os.fstat(capture.fileno()).st_size
        return os.pread(capture.fileno(), size, 0).decode(errors="replace")

    @staticmethod
    def _write_log(log: Path, text: str, prompt: str, sensitive: tuple[str, ...]) -> str:
        safe_text = text.replace(prompt, REDACTED_PROMPT) if prompt else text
        for secret in sensitive:
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
