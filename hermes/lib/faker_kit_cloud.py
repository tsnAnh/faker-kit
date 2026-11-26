from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from faker_kit_claude_cli import claude_command
from faker_kit_env import CLAUDE_CONFIG_DIR

SESSION = re.compile(r"https://claude\.ai/code/[A-Za-z0-9_-]+|\b(?:session|cse)_[A-Za-z0-9]{6,}\b")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[PX^_][^\x1b]*\x1b\\|\x1b[ -/]*[0-~]")
SESSION_ID = re.compile(r"https://claude\.ai/code/[A-Za-z0-9_-]+|(?:session|cse)_[A-Za-z0-9]{6,}")
STEER_SECONDS = 120
TRUST_QUESTION = re.compile(r"trust\s*this\s*folder", re.I)
KEEP_SECONDS = 3 * 24 * 3600
CHECKOUT = "repo"
CLAUDE_GLOBAL_CONFIG = ".claude.json"
CLAUDE_LEGACY_CONFIG = ".config.json"
REDACTED_PROMPT = "[redacted prompt]"
GIT_TIMEOUT_SECONDS = 120
GIT_GRACE_SECONDS = 10
STALE_LOCK_SECONDS = 600
GIT_TIMEOUT_ENV = "FAKER_KIT_GIT_TIMEOUT_SECONDS"
PROC_DIR_ENV = "FAKER_KIT_PROC_DIR"
LOCK_NAMES = ("index.lock", "shallow.lock", "HEAD.lock")
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_CEILING = "high"
DEFAULT_EFFORT = "medium"
DEFAULT_MODEL = "sonnet"
PERMISSION_MODE = "auto"

Runner = Callable[..., str]


class CloudError(Exception):
    pass


def session_effort(requested: str | None) -> tuple[str, dict[str, str | dict[str, str]]]:
    if requested is None:
        return DEFAULT_EFFORT, {"effort_defaulted": DEFAULT_EFFORT}
    if requested not in EFFORT_LEVELS:
        raise CloudError(f"effort takes one of {', '.join(EFFORT_LEVELS)}")
    if EFFORT_LEVELS.index(requested) > EFFORT_LEVELS.index(EFFORT_CEILING):
        return EFFORT_CEILING, {"effort_lowered": {"from": requested, "to": EFFORT_CEILING}}
    return requested, {}


def session_model(requested: str | None) -> tuple[str, dict[str, str]]:
    if requested is None:
        return DEFAULT_MODEL, {"model_defaulted": DEFAULT_MODEL}
    return requested, {}


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


def claude_global_config(config_dir: str) -> Path:
    legacy = (Path(config_dir) if config_dir else Path.home() / ".claude") / CLAUDE_LEGACY_CONFIG
    return legacy if legacy.exists() else (Path(config_dir) if config_dir else Path.home()) / CLAUDE_GLOBAL_CONFIG


def trust_checkout(checkout: Path, config_dir: str) -> Path | None:
    try:
        config = claude_global_config(config_dir).resolve(strict=True)
        data = json.loads(config.read_text())
        mode = config.stat().st_mode & 0o777
    except (OSError, ValueError):
        return None
    projects = data.setdefault("projects", {}) if isinstance(data, dict) else None
    entry = projects.setdefault(str(checkout.resolve()), {}) if isinstance(projects, dict) else None
    if not isinstance(entry, dict):
        return None
    if entry.get("hasTrustDialogAccepted") is True:
        return config
    entry["hasTrustDialogAccepted"] = True
    temp = config.with_name(f".{config.name}.{os.getpid()}.trust")
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "w") as out:
            json.dump(data, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        temp.chmod(mode)
        temp.replace(config)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
        return None
    return config


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


def run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    argv = ["git", "-C", str(path), *args]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=float(os.environ.get(GIT_TIMEOUT_ENV) or GIT_TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.communicate(timeout=GIT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        raise
    return subprocess.CompletedProcess(argv, proc.returncode, out, err)


def git_locks(git_dir: Path) -> list[Path]:
    found = [git_dir / name for name in LOCK_NAMES if (git_dir / name).is_file()]
    refs = git_dir / "refs"
    return found + (sorted(refs.rglob("*.lock")) if refs.is_dir() else [])


def inside(cwd: str, root: str) -> bool:
    return cwd == root or cwd.startswith(root + os.sep)


def git_holders(repo: Path) -> list[int] | None:
    root = os.path.realpath(repo)
    proc = Path(os.environ.get(PROC_DIR_ENV, "/proc"))
    if proc.is_dir():
        found = []
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                name = (entry / "comm").read_text().strip()
                cwd = str((entry / "cwd").readlink())
            except OSError:
                continue
            if name.startswith("git") and inside(cwd, root):
                found.append(int(entry.name))
        return found
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    try:
        done = subprocess.run([lsof, "-a", "-c", "git", "-d", "cwd", "-Fpn"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode not in (0, 1):
        return None
    found, pid = [], 0
    for line in done.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid and inside(line[1:], root):
            found.append(pid)
    return found


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
    holders: Callable[[Path], list[int] | None] = field(default_factory=lambda: git_holders)

    def start(self, repo: str, prompt: str, workdir: Path, model: str | None = None, effort: str | None = None) -> str:
        if not prompt.strip() or prompt.startswith("-"):
            raise CloudError("the task for a Claude Code session must be text that does not start with -")
        workdir.parent.mkdir(parents=True, exist_ok=True)
        self.prune(workdir.parent)
        checkout = self.checkout(repo, workdir.parent / CHECKOUT)
        trusted_in = trust_checkout(checkout, os.environ.get(CLAUDE_CONFIG_DIR, ""))
        chosen = ["--model", session_model(model)[0], "--effort", session_effort(effort)[0], "--permission-mode", PERMISSION_MODE]
        return self.launch([claude_command(), *chosen, "--cloud", prompt], checkout, workdir.with_name(workdir.name + ".log"), prompt, trusted_in)

    def steer(self, session: str, message: str) -> None:
        if not SESSION_ID.fullmatch(session):
            raise CloudError("a session to steer is named by its session_ id or its claude.ai/code link")
        try:
            done = subprocess.run([claude_command(), "-p", "--cloud", session, message], capture_output=True, text=True, env=child_env(), stdin=subprocess.DEVNULL, timeout=STEER_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            raise CloudError("claude did not send the message") from None
        try:
            sent = done.returncode == 0 and json.loads(done.stdout).get("ok") is True
        except (ValueError, AttributeError):
            sent = False
        if not sent:
            raise CloudError(f"claude did not accept the message (exit {done.returncode})")

    def checkout(self, repo: str, path: Path) -> Path:
        if not (path / ".git").exists():
            shutil.rmtree(path, ignore_errors=True)
            self.run_gh(["repo", "clone", repo, str(path), "--", "--depth", "1", "--quiet"])
            return path
        self.clear_stale_locks(path)
        try:
            return self.update(path)
        except CloudError as err:
            if ".lock" not in str(err) or not self.clear_stale_locks(path):
                raise
        return self.update(path)

    def update(self, path: Path) -> Path:
        for args in (["fetch", "--quiet", "--depth", "1", "origin", "HEAD"], ["reset", "--quiet", "--hard", "FETCH_HEAD"]):
            try:
                done = run_git(path, args)
            except (OSError, subprocess.TimeoutExpired):
                raise CloudError(f"git {args[0]} did not finish in {path}") from None
            if done.returncode:
                raise CloudError(f"git {args[0]} failed in {path}: {done.stderr.strip()[:200]}")
        return path

    def clear_stale_locks(self, path: Path) -> list[Path]:
        removed: list[Path] = []
        for lock in git_locks(path / ".git"):
            try:
                age = int(self.now() - lock.stat().st_mtime)
            except OSError:
                continue
            if age < STALE_LOCK_SECONDS:
                raise CloudError(f"{lock} is only {age}s old, so git may still be using it; it is left in place (a lock is stale after {STALE_LOCK_SECONDS}s)")
            held = self.holders(path)
            if held is None:
                raise CloudError(f"{lock} is {age}s old but faker-kit cannot tell whether a git process holds it (no /proc and no lsof); it is left in place")
            if held:
                raise CloudError(f"{lock} is {age}s old but git process {held[0]} is running in {path}; it is left in place")
            lock.unlink(missing_ok=True)
            removed.append(lock)
            sys.stderr.write(f"removed stale {lock} older than {age}s\n")
        return removed

    def launch(self, argv: list[str], cwd: Path, log: Path, prompt: str, trusted_in: Path | None = None) -> str:
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
                    why = (f"faker-kit marked it trusted in {trusted_in}, but Claude Code read another config; check the launch's CLAUDE_CONFIG_DIR"
                           if trusted_in else "faker-kit could not mark it trusted because Claude Code's config is missing or unreadable; sign Claude Code in on this machine")
                    raise CloudError(f"Claude Code asks whether to trust {cwd}: {why}")
                code = proc.poll()
                if code is not None:
                    text = clean_terminal_text(raw + read_available(master, 0.05))
                    found = SESSION.search(text)
                    safe = write_log(log, text, prompt)
                    if found:
                        return found.group(0)
                    last = next((line for line in reversed(safe.splitlines()) if line.strip()), "no output")
                    raise CloudError(f"{Path(argv[0]).name} exited {code} before starting a session: {last[:200]}")
                if self.clock() >= deadline:
                    stop(proc)
                    write_log(log, clean_terminal_text(raw + read_available(master, 0.05)), prompt)
                    raise CloudError(f"{Path(argv[0]).name} printed no session in {int(self.wait_seconds)}s; see {log}")
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
