import hashlib
import json
import math
import os
import pwd
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

KIT_OWNED = True
WRAPPER = "@WRAPPER@"
HERMES_HOME = "@HERMES_HOME@"
KIT_HOME = "@FAKER_KIT_HOME@"
PATH = "@PATH@"
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
TIMEOUT_SECONDS = 240
STDERR_TAIL = 400
HEARTBEAT_SECONDS = 6 * 60 * 60
NOTICE = "faker-kit notice:"


@dataclass(frozen=True)
class GateState:
    fingerprint: str
    woke_at: float


def fail(message: str, stderr: str = "") -> NoReturn:
    sys.stderr.write(message.strip() + "\n")
    tail = " ".join(stderr.split())[-STDERR_TAIL:]
    if tail:
        sys.stderr.write(f"stderr tail: {tail}\n")
    sys.exit(1)


def child_env() -> dict[str, str]:
    path = [part for part in PATH.split(os.pathsep) if part]
    path += [part for part in SYSTEM_PATH if part not in path]
    return {
        "HERMES_HOME": os.environ.get("HERMES_HOME") or HERMES_HOME,
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "PATH": os.pathsep.join(path),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def run_kit(command: str) -> subprocess.CompletedProcess[str]:
    try:
        done = subprocess.run([WRAPPER, command], env=child_env(), capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        fail(f"faker-kit {command} did not run: {type(err).__name__}")
    if done.returncode != 0:
        fail(f"faker-kit {command} exited {done.returncode}", done.stderr)
    return done


def parse_json(done: subprocess.CompletedProcess[str], command: str) -> object:
    try:
        return json.loads(done.stdout)
    except ValueError:
        fail(f"faker-kit {command} printed no JSON", done.stderr)


def status_lines() -> list[str]:
    done = run_kit("status")
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if not lines:
        fail("faker-kit status printed nothing", done.stderr)
    return lines


def state_path(job: str) -> Path:
    home = Path(os.environ.get("HERMES_HOME") or HERMES_HOME)
    bot = home.name if home.parent.name == "profiles" else "default"
    return Path(KIT_HOME) / "gates" / bot / f"{job}.json"


def read_state(path: Path) -> GateState | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fingerprint, woke_at = data.get("fingerprint"), data.get("woke_at")
    if not isinstance(fingerprint, str) or isinstance(woke_at, bool) or not isinstance(woke_at, (int, float)) or not math.isfinite(woke_at):
        return None
    return GateState(fingerprint, float(woke_at))


def write_state(path: Path, state: GateState) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            out.write(json.dumps({"fingerprint": state.fingerprint, "woke_at": state.woke_at}))
        temp.replace(path)
    except OSError as err:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
        fail(f"could not store the gate state in {path.parent}: {type(err).__name__}")


def fingerprint(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def wake_reason(stored: GateState | None, lines: list[str], now: float, news: str | None) -> str | None:
    if news:
        return news
    if stored is None:
        return "no earlier tick of this gate is recorded"
    if any(NOTICE in line for line in lines):
        return "a faker-kit notice printed"
    if stored.fingerprint != fingerprint(lines):
        return "the status changed"
    elapsed = now - stored.woke_at
    if elapsed < 0 or elapsed >= HEARTBEAT_SECONDS:
        return "six hours passed since this gate last woke the agent"
    return None


def tick(job: str, lines: list[str], now: float, news: str | None = None) -> str | None:
    path = state_path(job)
    reason = wake_reason(read_state(path), lines, now, news)
    if reason is not None:
        write_state(path, GateState(fingerprint(lines), now))
    return reason


def quiet() -> None:
    sys.stdout.write(json.dumps({"wakeAgent": False}) + "\n")


def say(*lines: str) -> None:
    sys.stdout.write("\n".join(lines) + "\n")
