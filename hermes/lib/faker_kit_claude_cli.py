from __future__ import annotations

import contextlib
import json
import os
import pwd
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from faker_kit_gh import Refused

CLAUDE_BIN_ENV = "FAKER_KIT_CLAUDE_BIN"
SYSTEM_DIRS_ENV = "FAKER_KIT_CLAUDE_SYSTEM_DIRS"
RECORD_FILE = "claude-path"
SYSTEM_DIRS = ("/usr/local/bin", "/opt/homebrew/bin")
PROBE_SECONDS = 30
NPM_SECONDS = 20
SUBSCRIPTION_MIN_CLI = (2, 1, 284)
INSTALL_FIX = "npm install -g @anthropic-ai/claude-code, then claude auth login"
HOME_BIN_DIRS = (".local/bin", ".npm-global/bin", ".claude/local", ".volta/bin", ".local/share/fnm/aliases/default/bin",
                 ".fnm/aliases/default/bin", "Library/Application Support/fnm/aliases/default/bin")
NVM_BIN_GLOB = ".nvm/versions/node/*/bin"

Probe = Callable[[Path], bool]
NpmPrefix = Callable[[Path], Path | None]


class Resolution(NamedTuple):
    path: str | None
    looked: tuple[str, ...]


def answers_version(path: Path) -> bool:
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        return subprocess.run([str(path), "--version"], capture_output=True, timeout=PROBE_SECONDS, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def global_npm_prefix(npm: Path) -> Path | None:
    try:
        done = subprocess.run([str(npm), "prefix", "-g"], capture_output=True, text=True, timeout=NPM_SECONDS, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    prefix = done.stdout.strip()
    return Path(prefix) if done.returncode == 0 and prefix.startswith("/") else None


def account_homes(env: Mapping[str, str]) -> list[Path]:
    homes: list[Path] = []
    with contextlib.suppress(KeyError):
        homes.append(Path(pwd.getpwuid(os.getuid()).pw_dir))
    if env.get("HOME", "").startswith("/") and Path(env["HOME"]) not in homes:
        homes.append(Path(env["HOME"]))
    return homes


def kit_home(env: Mapping[str, str]) -> Path:
    if env.get("FAKER_KIT_HOME"):
        return Path(env["FAKER_KIT_HOME"])
    homes = account_homes(env)
    return (homes[0] if homes else Path.home()) / ".hermes" / "faker-kit"


def version_key(path: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", path)]


def known_places(env: Mapping[str, str]) -> list[tuple[str, list[Path]]]:
    places: list[tuple[str, list[Path]]] = []
    for home in account_homes(env):
        for relative in HOME_BIN_DIRS:
            places.append((str(home / relative), [home / relative]))
        found = sorted((str(p) for p in home.glob(NVM_BIN_GLOB)), key=version_key, reverse=True)
        places.append((str(home / NVM_BIN_GLOB), [Path(p) for p in found]))
    system = env[SYSTEM_DIRS_ENV].split(os.pathsep) if SYSTEM_DIRS_ENV in env else list(SYSTEM_DIRS)
    places.extend((d, [Path(d)]) for d in system if d)
    return places


def path_dirs(env: Mapping[str, str]) -> list[Path]:
    return [Path(d).absolute() for d in env.get("PATH", "").split(os.pathsep) if d]


def record(home: Path, path: str) -> None:
    target = home / RECORD_FILE
    try:
        if not home.is_dir() or (target.is_file() and target.read_text().strip() == path):
            return
        fd, temp = tempfile.mkstemp(prefix=f".{RECORD_FILE}.", dir=home)
        with os.fdopen(fd, "w") as out:
            out.write(path + "\n")
        Path(temp).replace(target)
    except OSError:
        pass


def resolve_claude(home: Path | None = None, env: Mapping[str, str] | None = None, probe: Probe = answers_version,
                   npm_prefix: NpmPrefix = global_npm_prefix) -> Resolution:
    env = os.environ if env is None else env
    home = home or kit_home(env)
    looked: list[str] = []

    def done(path: Path) -> Resolution:
        record(home, str(path))
        return Resolution(str(path), tuple(looked))

    given = env.get(CLAUDE_BIN_ENV)
    if given:
        looked.append(f"{CLAUDE_BIN_ENV}={given}")
        if given.startswith("/") and probe(Path(given)):
            return done(Path(given))
    dirs = path_dirs(env)
    looked.append(f"PATH ({env.get('PATH', '')})")
    for directory in dirs:
        if probe(directory / "claude"):
            return done(directory / "claude")
    recorded_file = home / RECORD_FILE
    try:
        recorded = recorded_file.read_text().strip()
    except OSError:
        recorded = ""
    looked.append(f"the path recorded in {recorded_file} ({recorded or 'none'})")
    if recorded.startswith("/") and probe(Path(recorded)):
        return done(Path(recorded))
    places = known_places(env)
    for label, folders in places:
        looked.append(label)
        for folder in folders:
            if probe(folder / "claude"):
                return done(folder / "claude")
    npm = next((d / "npm" for d in [*dirs, *(f for _, folders in places for f in folders)] if (d / "npm").is_file() and os.access(d / "npm", os.X_OK)), None)
    prefix = npm_prefix(npm) if npm else None
    looked.append(f"{prefix / 'bin'} (npm prefix -g)" if prefix else "the global npm folder (npm was not found or gave no prefix)")
    if prefix and probe(prefix / "bin" / "claude"):
        return done(prefix / "bin" / "claude")
    return Resolution(None, tuple(looked))


def claude_command() -> str:
    return resolve_claude().path or "claude"


def missing_message(looked: tuple[str, ...], then: str) -> str:
    return f"Claude Code was not found. Looked in: {'; '.join(looked)}. Fix: {INSTALL_FIX}, then {then}"


def check_login(claude: str) -> None:
    try:
        version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=PROBE_SECONDS).stdout
        status = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=PROBE_SECONDS).stdout
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("Claude Code did not answer --version or auth status") from None
    found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", version)
    if not found or tuple(int(n) for n in found.groups()) < SUBSCRIPTION_MIN_CLI:
        raise Refused(f"Claude Code {'.'.join(map(str, SUBSCRIPTION_MIN_CLI))} or later is needed; update it with npm install -g @anthropic-ai/claude-code")
    try:
        logged_in = json.loads(status).get("loggedIn") is True
    except (ValueError, AttributeError):
        logged_in = False
    if not logged_in:
        raise Refused("Claude Code is not logged in here: run claude auth login, then run this again")
