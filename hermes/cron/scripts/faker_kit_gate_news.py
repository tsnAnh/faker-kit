import json
import os
import subprocess
from contextlib import suppress
from datetime import datetime
from pathlib import Path

import faker_kit_cron_list as cron_list
import faker_kit_gate as gate

KIT_OWNED = True
JOB = "faker-kit-chief-news"
SETTLED = "ok"
READY = "ready to merge"


def state_path() -> Path:
    return Path(gate.KIT_HOME) / "chief-news" / f"{gate.profile_name()}.json"


def read_news() -> dict:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def pending_lines() -> list[str]:
    pending = read_news().get("pending")
    return [str(entry["line"]) for entry in pending if isinstance(entry, dict) and "line" in entry] if isinstance(pending, list) else []


def write_news(data: dict) -> None:
    path = state_path()
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            out.write(json.dumps(data, sort_keys=True))
        temp.replace(path)
    except OSError:
        with suppress(OSError):
            temp.unlink(missing_ok=True)


def tracked() -> list[dict] | None:
    try:
        done = subprocess.run([gate.WRAPPER, "news"], env=gate.child_env(), capture_output=True, text=True, timeout=gate.TIMEOUT_SECONDS, check=False)
        rows = json.loads(done.stdout).get("tracked") if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        return None
    return [row for row in rows if isinstance(row, dict) and {"unit", "number", "url", "state", "draft", "head"} <= set(row)] if isinstance(rows, list) else None


def ready_numbers(lines: list[str]) -> set[int]:
    ready = set()
    for line in lines:
        fields = gate.pull_fields(line)
        if fields and fields[-1] == READY and "ready" in fields:
            ready.add(int(fields[0][1:]))
    return ready


def events(rows: list[dict], ready: set[int]) -> list[tuple[str, str]]:
    found = []
    for row in rows:
        number, state = int(row["number"]), row["state"]
        kinds = [(state, "")] if state in ("merged", "closed") else [("opened", "")]
        if state == "open" and not row["draft"] and number in ready:
            kinds.append(("needs the owner", str(row["head"])))
        for kind, head in kinds:
            key = f"{number}|{kind}|{head}"
            found.append((key, f"Chief news: unit {row['unit']} pull request #{number} {row['url']} {kind}."))
    return found


def fire(profile: str, since: float) -> str | None:
    try:
        subprocess.run(["hermes", "-p", profile, "cron", "run", JOB], env=gate.child_env(), capture_output=True, text=True,
                       timeout=gate.HERMES_TIMEOUT_SECONDS, check=False)
        done = subprocess.run(["hermes", "-p", profile, "cron", "list", "--all"], env=gate.child_env(), capture_output=True, text=True,
                              timeout=gate.HERMES_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        return f"hermes did not run ({type(err).__name__})"
    entry = cron_list.cron_jobs(done.stdout).get(JOB) if done.returncode == 0 else None
    if entry is None:
        return f"the {JOB} job is not in this profile"
    last = cron_list.last_run(entry)
    try:
        ran = datetime.fromisoformat(last.at).timestamp() if last else None
    except ValueError:
        ran = None
    if last is None or ran is None or ran < since:
        return "the delivery run left no receipt"
    return None if last.outcome == SETTLED else "the delivery receipt has not settled"


def deliver(lines: list[str], now: float) -> str | None:
    rows = tracked()
    data = read_news()
    sent = set(data.get("sent") or [])
    pending = [entry for entry in data.get("pending") or [] if isinstance(entry, dict) and "key" in entry and "line" in entry]
    known = sent | {entry["key"] for entry in pending}
    if rows is not None:
        fresh = [(key, line) for key, line in events(rows, ready_numbers(lines)) if key not in known]
        if not data:
            sent |= {key for key, line in fresh if key.split("|")[1] in ("merged", "closed")}
            fresh = [(key, line) for key, line in fresh if key not in sent]
        pending += [{"key": key, "line": line} for key, line in fresh]
    if not pending:
        if not data and rows is not None:
            write_news({"sent": sorted(sent), "pending": [], "failure": None})
        return None
    write_news({"sent": sorted(sent), "pending": pending, "failure": data.get("failure")})
    failure = fire(gate.profile_name(), int(now))
    if failure is None:
        write_news({"sent": sorted(sent | {entry["key"] for entry in pending}), "pending": [], "failure": None})
        return None
    write_news({"sent": sorted(sent), "pending": pending, "failure": failure})
    if failure == data.get("failure"):
        return None
    return f"{len(pending)} chief news note(s) not delivered yet: {failure}; the next event wake retries them"
