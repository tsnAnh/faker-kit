from __future__ import annotations

import contextlib
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

SESSION = re.compile(r"https://claude\.ai/code/[A-Za-z0-9_-]+|\b(?:session|cse)_[A-Za-z0-9]{6,}\b")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[PX^_][^\x1b]*\x1b\\|\x1b[ -/]*[0-~]")
TRUST_QUESTION = re.compile(r"trust\s*this\s*folder", re.I)
KEEP_SECONDS = 3 * 24 * 3600
CHECKOUT = "repo"
REDACTED_PROMPT = "[redacted prompt]"
GIT_TIMEOUT_SECONDS = 120

Runner = Callable[..., str]


class CloudError(Exception):
    pass


def clean_terminal_text(raw: bytes) -> str:
    text = ANSI.sub("", raw.decode(errors="replace"))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_available(fd: int, timeout: float) -> bytes:
    data = b""
    try:
        ready, _, _ = select.select([fd], [], [], timeout)
        while ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            data += chunk
            ready, _, _ = select.select([fd], [], [], 0)
    except OSError:
        pass
    return data


def acquire_terminal() -> None:
    fcntl.ioctl(1, termios.TIOCSCTTY, 0)


def child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "CI"}


def reap(proc: subprocess.Popen[bytes], timeout: float | None = None) -> None:
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        proc.wait(timeout)


def drain(fd: int, proc: subprocess.Popen[bytes]) -> None:
    try:
        while True:
            select.select([fd], [], [])
            if not os.read(fd, 65536):
                break
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        reap(proc)


def stop(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, AttributeError):
        proc.kill()
    reap(proc, 5)


def write_log(log: Path, text: str, prompt: str) -> str:
    safe = text.replace(prompt, REDACTED_PROMPT) if prompt else text
    log.write_text(safe, encoding="utf-8", errors="replace")
    return safe


@dataclass
class Cloud:
    run_gh: Runner
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
    wait_seconds: float = 180.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], float] = field(default=time.time)

    def start(self, repo: str, prompt: str, workdir: Path) -> str:
        if not prompt.strip() or prompt.startswith("-"):
            raise CloudError("the task for a Claude Code session must be text that does not start with -")
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self.prune(workdir.parent)
        checkout = self.checkout(repo, workdir.parent / CHECKOUT)
        return self.launch(["claude", "--cloud", prompt], checkout, workdir.with_name(workdir.name + ".log"), prompt)

    def checkout(self, repo: str, path: Path) -> Path:
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

    def launch(self, argv: list[str], cwd: Path, log: Path, prompt: str) -> str:
        log.write_text("", encoding="utf-8")
        master, slave = os.openpty()
        keep_master = False
        try:
            proc = self.popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=slave, stderr=slave, env=child_env(),
                              start_new_session=True, preexec_fn=acquire_terminal)
            raw = b""
            deadline = self.clock() + self.wait_seconds
            while True:
                raw += read_available(master, 0.05)
                text = clean_terminal_text(raw)
                found = SESSION.search(text)
                if found:
                    write_log(log, text, prompt)
                    keep_master = True
                    os.close(slave)
                    slave = -1
                    threading.Thread(target=drain, args=(master, proc), daemon=True).start()
                    return found.group(0)
                if TRUST_QUESTION.search(text):
                    stop(proc)
                    write_log(log, text, prompt)
                    raise CloudError(f"Claude Code asks whether to trust {cwd}; trust it once: cd {cwd} && claude, choose yes, then /exit")
                code = proc.poll()
                if code is not None:
                    text = clean_terminal_text(raw + read_available(master, 0.05))
                    found = SESSION.search(text)
                    safe = write_log(log, text, prompt)
                    if found:
                        return found.group(0)
                    last = next((line for line in reversed(safe.splitlines()) if line.strip()), "no output")
                    raise CloudError(f"{argv[0]} exited {code} before starting a session: {last[:200]}")
                if self.clock() >= deadline:
                    stop(proc)
                    write_log(log, clean_terminal_text(raw + read_available(master, 0.05)), prompt)
                    raise CloudError(f"{argv[0]} printed no session in {int(self.wait_seconds)}s; see {log}")
                self.sleep(1.0)
        finally:
            for fd in ([slave] if slave >= 0 else []) + ([] if keep_master else [master]):
                with contextlib.suppress(OSError):
                    os.close(fd)

    def prune(self, parent: Path) -> None:
        cutoff = self.now() - KEEP_SECONDS
        for entry in parent.iterdir():
            if entry.name == CHECKOUT:
                continue
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
            except OSError:
                pass
