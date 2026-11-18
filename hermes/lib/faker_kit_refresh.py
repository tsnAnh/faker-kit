from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from faker_kit_claude_cli import CLAUDE_BIN_ENV, check_login, missing_message, resolve_claude
from faker_kit_gh import Coded, Refused
from faker_kit_machine import MachineError, load_machine

SHA = re.compile(r"\A[0-9a-f]{40}\Z")
SOURCE_FILE = "kit-source"
INSTALLED_FILE = "installed-ref"
LOCK_FILE = "refresh.lock"
MACHINE_FILE = "machine.yaml"
MAIN = "refs/heads/main"
INSTALL_ARGS = ("--no-gateway",)
LS_REMOTE_SECONDS = 60
FETCH_SECONDS = 300
INSTALL_SECONDS = 1380
SHORT = 12
NOTE_LINES = 3
NOTE_CHARS = 300


def short(sha: str) -> str:
    return sha[:SHORT]


def clean_source(source: str) -> str:
    parts = urlsplit(source)
    if parts.password is None and parts.username is None:
        return source
    host = parts.hostname or ""
    return parts._replace(netloc=f"{host}:{parts.port}" if parts.port else host).geturl()


def read_line(path: Path) -> str | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return text or None


def recorded_source(home: Path) -> str:
    source = read_line(home / SOURCE_FILE)
    if source:
        return source
    raise Coded(f"no kit source is recorded in {home}; run hermes/install.sh once from a kit clone with --source", "kit_source_missing", "contact_support")


def installed_commit(home: Path) -> str:
    recorded = read_line(home / INSTALLED_FILE)
    if recorded and SHA.match(recorded):
        return recorded
    done = subprocess.run(["git", "-C", str(home / "kit"), "rev-parse", "HEAD"], capture_output=True, text=True)
    commit = done.stdout.strip()
    if done.returncode or not SHA.match(commit):
        raise Coded(f"no kit checkout at {home / 'kit'}; run hermes/install.sh", "kit_install_failed", "contact_support")
    return commit


def git_env() -> dict[str, str]:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def note(text: str) -> str:
    lines = [" ".join(line.split())[:NOTE_CHARS] for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-NOTE_LINES:])


def run(argv: list[str], seconds: int, what: str, source: str, cwd: Path | None = None, env: dict[str, str] | None = None,
        code: str = "kit_fetch_failed") -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, cwd=cwd, env=env or git_env())
    except subprocess.TimeoutExpired:
        raise Coded(f"{what} did not finish within {seconds}s ({clean_source(source)})", code) from None
    except OSError as err:
        raise Coded(f"{what} could not run: {err.strerror or err}", code) from None
    if done.returncode:
        raise Coded(f"{what} failed ({clean_source(source)}): {note(done.stderr) or note(done.stdout) or f'exit {done.returncode}'}", code)
    return done.stdout


def latest_main(source: str) -> str:
    out = run(["git", "ls-remote", source, MAIN], LS_REMOTE_SECONDS, "reading the kit source's main", source)
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == MAIN and SHA.match(sha):
            return sha
    raise Coded(f"the kit source has no main branch ({clean_source(source)})", "kit_fetch_failed")


@contextmanager
def exclusive(home: Path, name: str = LOCK_FILE) -> Iterator[None]:
    with (home / name).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Coded("another kit refresh is already running", "update_in_progress") from None
        yield


def fetch_kit(source: str, commit: str, into: Path) -> None:
    run(["git", "init", "-q", str(into)], FETCH_SECONDS, "preparing the kit checkout", source)
    run(["git", "-C", str(into), "fetch", "-q", "--depth", "1", source, commit], FETCH_SECONDS, "fetching the kit", source)
    run(["git", "-C", str(into), "checkout", "-q", "-f", "FETCH_HEAD"], FETCH_SECONDS, "checking out the kit", source)
    if run(["git", "-C", str(into), "rev-parse", "HEAD"], FETCH_SECONDS, "reading the fetched kit", source).strip() != commit:
        raise Coded(f"the fetched kit is not {short(commit)} ({clean_source(source)})", "kit_fetch_failed")


def uses_subscription(home: Path) -> bool:
    machine = home / MACHINE_FILE
    if not machine.is_file():
        return False
    try:
        return load_machine(machine).claude_subscription
    except MachineError:
        return False


def claude_preflight(home: Path) -> dict[str, str]:
    if not uses_subscription(home):
        return {}
    unchanged = "the installed kit was not changed"
    found = resolve_claude(home)
    if not found.path:
        raise Coded(f"{missing_message(found.looked, 'run the update again')}; {unchanged}", "kit_install_failed", "contact_support")
    try:
        check_login(found.path)
    except Refused as err:
        raise Coded(f"{err}; {unchanged}", "kit_install_failed", "contact_support") from None
    return {CLAUDE_BIN_ENV: found.path}


def install_kit(staged: Path, home: Path, source: str, commit: str, extra_env: dict[str, str]) -> None:
    script = staged / "hermes" / "install.sh"
    for needed in (script, staged / "hermes" / "bin" / "faker_kit.py"):
        if not needed.is_file():
            raise Coded(f"kit {short(commit)} has no {needed.relative_to(staged)}; the installed kit was not changed", "kit_install_failed")
    env = {**git_env(), **extra_env, "FAKER_KIT_HOME": str(home)}
    run(["bash", str(script), "--ref", commit, "--source", source, *INSTALL_ARGS], INSTALL_SECONDS,
        f"installing kit {short(commit)}", source, cwd=home, env=env, code="kit_install_failed")
    if installed_commit(home) != commit:
        raise Coded(f"the install of kit {short(commit)} finished without recording it; run it again", "kit_install_failed")


def refresh(home: Path) -> str:
    source = recorded_source(home)
    latest = latest_main(source)
    with exclusive(home):
        before = installed_commit(home)
        if before == latest:
            return f"kit already current at {short(latest)}"
        install_env = claude_preflight(home)
        staging = Path(tempfile.mkdtemp(prefix=".refresh-", dir=home))
        try:
            fetch_kit(source, latest, staging)
            install_kit(staging, home, source, latest, install_env)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return f"kit moved {short(before)} -> {short(latest)}"
