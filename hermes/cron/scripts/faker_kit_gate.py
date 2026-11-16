import hashlib
import json
import math
import os
import pwd
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn

import faker_kit_cron_list as cron_list

KIT_OWNED = True
WRAPPER = "@WRAPPER@"
HERMES_HOME = "@HERMES_HOME@"
KIT_HOME = "@FAKER_KIT_HOME@"
PATH = "@PATH@"
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
TIMEOUT_SECONDS = 240
HERMES_TIMEOUT_SECONDS = 60
STDERR_TAIL = 400
HEARTBEAT_SECONDS = 6 * 60 * 60
MAX_RUN_RETRIES = 3
NOTICE = "faker-kit notice:"
NO_PULLS = "no open pull requests"
NOT_RECORDED = "no earlier tick of this gate is recorded"
NOTICE_REASON = "a faker-kit notice printed"
CHANGED = "the status changed"
LAST_GONE = "the last open pull request merged or closed"
HEARTBEAT_REASON = "six hours passed since this gate last woke the agent while a pull request is open"
RUN_FAILED = "the job's previous run after the last wake did not finish ok"


@dataclass(frozen=True)
class GateState:
    fingerprint: str
    woke_at: float
    retries: int = 0


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


def run_kit(*argv: str) -> subprocess.CompletedProcess[str]:
    command = " ".join(argv)
    try:
        done = subprocess.run([WRAPPER, *argv], env=child_env(), capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
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


def pull_fields(line: str) -> list[str] | None:
    fields = line.split(" | ")
    return fields if len(fields) > 3 and fields[0].startswith("#") and fields[0][1:].isdecimal() else None


def pull_label(line: str) -> str | None:
    fields = pull_fields(line)
    if fields is None:
        return None
    for index, field in enumerate(fields[1:-1], 1):
        if field in ("draft", "ready"):
            return fields[index + 1] if fields[index + 1] in ("labeled", "unlabeled") else None
    return None


def labeled_lines(lines: list[str]) -> list[str]:
    dropped = [line for line in lines if pull_label(line) == "unlabeled"]
    kept = [line for line in lines if line not in dropped]
    if dropped and not any(pull_fields(line) for line in kept) and NO_PULLS not in kept:
        kept.append(NO_PULLS)
    return kept


def profile_name() -> str:
    home = Path(os.environ.get("HERMES_HOME") or HERMES_HOME)
    return home.name if home.parent.name == "profiles" else "default"


def state_path(job: str) -> Path:
    return Path(KIT_HOME) / "gates" / profile_name() / f"{job}.json"


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
    retries = data.get("retries", 0)
    return GateState(fingerprint, float(woke_at), retries if isinstance(retries, int) and not isinstance(retries, bool) and retries > 0 else 0)


def write_state(path: Path, state: GateState) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            out.write(json.dumps({"fingerprint": state.fingerprint, "woke_at": state.woke_at, "retries": state.retries}))
        temp.replace(path)
    except OSError as err:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
        fail(f"could not store the gate state in {path.parent}: {type(err).__name__}")


def fingerprint(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def failed_run_reason(job: str, since: float) -> str | None:
    try:
        done = subprocess.run(["hermes", "-p", profile_name(), "cron", "list", "--all"], env=child_env(), capture_output=True, text=True,
                              timeout=HERMES_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    entry = cron_list.cron_jobs(done.stdout).get(job) if done.returncode == 0 else None
    last = cron_list.last_run(entry) if entry is not None else None
    if last is None:
        return None
    try:
        ran = datetime.fromisoformat(last.at).timestamp()
    except ValueError:
        return None
    return RUN_FAILED if ran > since and not last.finished else None


def wake_reason(job: str, stored: GateState | None, lines: list[str], now: float, news: str | None) -> str | None:
    if news:
        return news
    if any(NOTICE in line for line in lines):
        return NOTICE_REASON
    if lines == [NO_PULLS]:
        return LAST_GONE if stored is not None and stored.fingerprint != fingerprint(lines) else None
    if stored is None:
        return NOT_RECORDED
    if stored.fingerprint != fingerprint(lines):
        return CHANGED
    elapsed = now - stored.woke_at
    if elapsed < 0 or elapsed >= HEARTBEAT_SECONDS:
        return HEARTBEAT_REASON
    if stored.retries >= MAX_RUN_RETRIES:
        return None
    return failed_run_reason(job, stored.woke_at)


def tick(job: str, lines: list[str], now: float, news: str | None = None) -> str | None:
    path = state_path(job)
    stored = read_state(path)
    reason = wake_reason(job, stored, lines, now, news)
    empty = NO_PULLS in lines
    current = fingerprint([line for line in lines if NOTICE not in line] if empty else lines)
    if reason is not None:
        retries = stored.retries + 1 if stored is not None and reason == RUN_FAILED else 0
        write_state(path, GateState(current, now, retries))
    elif lines == [NO_PULLS] and (stored is None or stored.fingerprint != current):
        write_state(path, GateState(current, stored.woke_at if stored else now))
    return reason


def quiet() -> None:
    sys.stdout.write(json.dumps({"wakeAgent": False}) + "\n")


def say(*lines: str) -> None:
    sys.stdout.write("\n".join(lines) + "\n")
