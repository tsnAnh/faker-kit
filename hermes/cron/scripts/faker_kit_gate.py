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
MAX_ASKED = 200
MAX_RUN_RETRIES = 3
NOTICE = "faker-kit notice:"
NO_PULLS = "no open pull requests"
BLOCKED = "blocked:"
SHA_LENGTH = 40
NOT_RECORDED = "no earlier tick of this gate is recorded"
NOTICE_REASON = "a faker-kit notice printed"
CHANGED = "the status changed"
LAST_GONE = "the last open pull request merged or closed"
HEARTBEAT_REASON = "six hours passed since this gate last woke the agent while a pull request is open"
RUN_FAILED = "the job's previous run after the last wake did not finish ok"


@dataclass(frozen=True)
class Mark:
    line: str
    stable: str
    blocked: bool


@dataclass(frozen=True)
class GateState:
    fingerprint: str
    woke_at: float
    retries: int = 0
    marks: dict[str, Mark] | None = None
    asked: tuple[str, ...] = ()
    units: dict[str, str] | None = None


@dataclass(frozen=True)
class Changes:
    shown: list[str]
    gone: list[str]

    @property
    def any(self) -> bool:
        return bool(self.shown or self.gone)


@dataclass(frozen=True)
class Wake:
    reason: str
    lines: list[str]
    summary: str | None = None


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


def collapse(lines: list[str]) -> list[str]:
    kept: dict[str, str] = {}
    for line in lines:
        kept[mark_key(line)] = line
    return list(kept.values())


def digest_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def stable_line(fields: list[str]) -> str:
    return " | ".join("updated" if field.startswith("updated ") else field for field in fields)


def mark_key(line: str) -> str:
    fields = pull_fields(line)
    return fields[0] if fields else f"line:{digest_of(line)[:16]}"


def marks_of(lines: list[str]) -> dict[str, Mark]:
    found: dict[str, Mark] = {}
    for line in lines:
        fields = pull_fields(line)
        if fields:
            found[fields[0]] = Mark(digest_of(line), digest_of(stable_line(fields)), fields[-1].startswith(BLOCKED))
        elif NOTICE not in line and line != NO_PULLS:
            found[mark_key(line)] = Mark(digest_of(line), digest_of(line), False)
    return found


def changes_since(stored: GateState | None, lines: list[str]) -> Changes:
    marks = marks_of(lines)
    before = stored.marks if stored is not None and stored.marks is not None else {}
    shown = []
    for line in lines:
        now = marks.get(mark_key(line))
        if now is None:
            continue
        old = before.get(mark_key(line))
        if old is not None and old.line == now.line:
            continue
        if old is not None and old.stable == now.stable and not old.blocked and not now.blocked:
            continue
        shown.append(line)
    gone = [f"{key} merged or closed" for key in before if key.startswith("#") and key not in marks]
    return Changes(shown, gone)


def profile_name() -> str:
    home = Path(os.environ.get("HERMES_HOME") or HERMES_HOME)
    return home.name if home.parent.name == "profiles" else "default"


def state_path(job: str) -> Path:
    return Path(KIT_HOME) / "gates" / profile_name() / f"{job}.json"


def read_marks(raw: object) -> dict[str, Mark] | None:
    if not isinstance(raw, dict):
        return None
    found = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            return None
        line, stable, blocked = entry.get("line"), entry.get("stable"), entry.get("blocked")
        if not isinstance(line, str) or not isinstance(stable, str) or not isinstance(blocked, bool):
            return None
        found[key] = Mark(line, stable, blocked)
    return found


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
    asked, units = data.get("asked"), data.get("units")
    return GateState(fingerprint, float(woke_at), retries if isinstance(retries, int) and not isinstance(retries, bool) and retries > 0 else 0, read_marks(data.get("pulls")),
                     tuple(a for a in asked if isinstance(a, str)) if isinstance(asked, list) else (),
                     {k: v for k, v in units.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(units, dict) else None)


def write_state(path: Path, state: GateState) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            pulls = {key: {"line": mark.line, "stable": mark.stable, "blocked": mark.blocked} for key, mark in (state.marks or {}).items()}
            out.write(json.dumps({"fingerprint": state.fingerprint, "woke_at": state.woke_at, "retries": state.retries, "pulls": pulls,
                                "asked": list(state.asked[-MAX_ASKED:]), "units": state.units or {}}))
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


def wake_reason(job: str, stored: GateState | None, lines: list[str], now: float, news: str | None, change: Changes) -> str | None:
    if news:
        return news
    if any(NOTICE in line for line in lines):
        return NOTICE_REASON
    if lines == [NO_PULLS]:
        return LAST_GONE if stored is not None and stored.fingerprint != fingerprint(lines) else None
    if stored is None:
        return NOT_RECORDED
    if stored.fingerprint != fingerprint(lines) and change.any:
        return CHANGED
    elapsed = now - stored.woke_at
    if elapsed < 0 or elapsed >= HEARTBEAT_SECONDS:
        return HEARTBEAT_REASON
    if stored.retries >= MAX_RUN_RETRIES:
        return None
    return failed_run_reason(job, stored.woke_at)


def unit_heads(lines: list[str]) -> dict[str, str]:
    found = {}
    for line in lines:
        fields = pull_fields(line)
        unit = next((f[5:] for f in fields[1:] if f.startswith("unit ")), None) if fields else None
        head = next((f[5:] for f in fields[1:] if f.startswith("head ")), None) if fields else None
        if fields and unit and head and len(head) == SHA_LENGTH:
            found[fields[0]] = f"{unit}|{head}|{'draft' if 'draft' in fields else 'ready'}"
    return found


def summaries_due(stored: GateState | None, shown: list[str], gone: list[str]) -> dict[str, str]:
    asked = set(stored.asked) if stored is not None else set()
    previous = stored.units if stored is not None and stored.units else {}
    due = {}
    for number, entry in unit_heads(shown).items():
        due[entry] = number
    for line in gone:
        number = line.split(" ", 1)[0]
        if number in previous:
            unit, head, _ = previous[number].split("|")
            due[f"{unit}|{head}|ended"] = number
    return {entry: number for entry, number in due.items() if entry not in asked}


def summary_line(due: dict[str, str]) -> str:
    named = ", ".join(f"unit {entry.split('|')[0]} ({number}, head {entry.split('|')[1][:7]}, {entry.split('|')[2]})" for entry, number in sorted(due.items()))
    return (f"Closing summaries to read: {named}. Run faker-kit session summary <unit> for each before you update its card, decide the next step, or report to the chief. "
            "The summary is the session's claim, never proof: verify the pull request, head, checks, and artifact on GitHub before acting on it.")


def tick(job: str, lines: list[str], now: float, news: str | None = None, summaries: bool = False) -> Wake | None:
    path = state_path(job)
    stored = read_state(path)
    change = changes_since(stored, lines)
    reason = wake_reason(job, stored, lines, now, news, change)
    empty = NO_PULLS in lines
    kept = [line for line in lines if NOTICE not in line] if empty else lines
    current = fingerprint(kept)
    marks = marks_of(lines)
    wake = None
    if reason is not None:
        wake = Wake(reason, [*change.shown, *change.gone] if reason == CHANGED else [*change.gone, *lines] if reason == LAST_GONE else lines)
    due = summaries_due(stored, [line for line in wake.lines if pull_fields(line)], [line for line in wake.lines if line.endswith(" merged or closed")]) if summaries and wake else {}
    asked = (*(stored.asked if stored else ()), *due)
    units = unit_heads(lines) if summaries else (stored.units if stored else None)
    if wake is not None:
        retries = stored.retries + 1 if stored is not None and reason == RUN_FAILED else 0
        write_state(path, GateState(current, now, retries, marks, asked, units))
    elif lines == [NO_PULLS] and (stored is None or stored.fingerprint != current):
        write_state(path, GateState(current, stored.woke_at if stored else now, 0, marks, stored.asked if stored else (), units))
    if wake is None:
        return None
    return Wake(wake.reason, wake.lines, summary_line(due) if due else None)


def quiet() -> None:
    sys.stdout.write(json.dumps({"wakeAgent": False}) + "\n")


def say(*lines: str) -> None:
    sys.stdout.write("\n".join(lines) + "\n")
