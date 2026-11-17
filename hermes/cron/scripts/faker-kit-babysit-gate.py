import json
import os
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import faker_kit_gate as gate
import faker_kit_gate_news as chief_news

KIT_OWNED = True
JOB = "faker-kit-babysit"


def listed_issues() -> list[object]:
    done = gate.run_kit("issues")
    report = gate.parse_json(done, "issues")
    issues = report.get("issues") if isinstance(report, dict) else None
    if not isinstance(issues, list):
        gate.fail("faker-kit issues printed an unexpected report shape", done.stderr)
    return issues


def card_check_line() -> str | None:
    try:
        done = subprocess.run([gate.WRAPPER, "loop", "check"], env=gate.child_env(), capture_output=True, text=True, timeout=gate.TIMEOUT_SECONDS, check=False)
        report = json.loads(done.stdout) if done.returncode in (0, 4) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        report = None
    findings = report.get("findings") if isinstance(report, dict) else None
    if not isinstance(findings, list):
        return "faker-kit loop check could not read the board this tick; run faker-kit loop check"
    wrong = [f for f in findings if isinstance(f, dict) and f.get("needs_action")]
    if not wrong:
        return None
    return f"faker-kit loop check: {len(wrong)} card(s) need action, first: {wrong[0].get('line')}; run faker-kit loop check for every fix line"


def told_path(kind: str = "settle") -> Path:
    return Path(gate.KIT_HOME) / "gates" / gate.profile_name() / f"{JOB}-{kind}.json"


def read_told(kind: str = "settle") -> set[str]:
    try:
        data = json.loads(told_path(kind).read_text())
    except (OSError, ValueError):
        return set()
    return {key for key in data if isinstance(key, str)} if isinstance(data, list) else set()


def write_told(keys: set[str], kind: str = "settle") -> None:
    path = told_path(kind)
    temp = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as out:
            out.write(json.dumps(sorted(keys)))
        temp.replace(path)
    except OSError:
        with suppress(OSError):
            temp.unlink(missing_ok=True)


def settle_cards() -> dict[str, str]:
    try:
        done = subprocess.run([gate.WRAPPER, "loop", "reconcile"], env=gate.child_env(), capture_output=True, text=True, timeout=gate.TIMEOUT_SECONDS, check=False)
        report = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        report = None
    entries = report.get("entries") if isinstance(report, dict) else None
    if not isinstance(entries, dict):
        return {"could-not-run": "faker-kit loop reconcile could not settle the unit cards this tick; run faker-kit loop reconcile"}
    found: dict[str, str] = {}
    for row in report.get("blocked") or []:
        if isinstance(row, dict) and row.get("blocked") is True:
            found[f"blocked:{row.get('card')}"] = (f"faker-kit loop reconcile: unit {row.get('unit')} (card {row.get('card')}) was blocked: its session left no pull request after the one steer. "
                                                   "Tell the owner once; the card is never picked up or relaunched")
        elif isinstance(row, dict):
            found[f"block-refused:{row.get('card')}:{row.get('detail', '')}"] = f"faker-kit loop reconcile: could not block unit {row.get('unit')} ({row.get('detail', '')}); the next pass retries"
    for row in entries.get("refused") or []:
        if isinstance(row, dict):
            card = row.get("card") or row.get("id")
            found[f"refused:{card}:{row.get('head', '')}"] = (
                f"faker-kit loop reconcile: card {card} was refused ({row.get('classification', 'unknown')}: {row.get('detail', '')}); "
                "its claim is released and the next tick retries")
    for row in entries.get("unreadable") or []:
        if isinstance(row, dict):
            found[f"unreadable:{row.get('id')}:{row.get('reason', '')}"] = f"faker-kit loop reconcile: could not read GitHub for unit {row.get('id')} ({row.get('reason', '')}); the next tick retries"
    return found


def pickup_lines() -> dict[str, str]:
    try:
        done = subprocess.run([gate.WRAPPER, "pickup", "status"], env=gate.child_env(), capture_output=True, text=True, timeout=gate.TIMEOUT_SECONDS, check=False)
        report = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        report = None
    rows = report.get("pickup") if isinstance(report, dict) and report.get("enabled") is True else None
    return {str(row["card"]): f"Ready card to pick up: {row['unit']} (card {row['card']})" for row in rows or []
            if isinstance(row, dict) and isinstance(row.get("unit"), str) and isinstance(row.get("card"), str)}


def main() -> None:
    issues = listed_issues()
    settled = settle_cards()
    told = read_told()
    refusals = [line for key, line in settled.items() if key not in told]
    if told != set(settled):
        write_told(set(settled))
    picked = pickup_lines()
    told_picks = read_told("pickup")
    fresh_picks = [line for key, line in picked.items() if key not in told_picks]
    if told_picks != set(picked):
        write_told(set(picked), "pickup")
    lines = gate.labeled_lines(gate.collapse(gate.status_lines()))
    news = f"faker-kit issues lists {len(issues)} issue(s) to handle" if issues else None
    undelivered = chief_news.deliver(lines, time.time())
    cards = card_check_line()
    ready = f"faker-kit pickup: {len(fresh_picks)} ready unit card(s) to pick up" if fresh_picks else None
    news = news or undelivered or cards or (refusals[0] if refusals else None) or ready
    wake = gate.tick(JOB, lines, time.time(), news, summaries=True, stuck=gate.stuck_units(time.time()))
    if wake is None:
        gate.quiet()
        return
    gate.say(
        f"The babysit gate woke this tick because {wake.reason}.",
        "faker-kit issues:",
        json.dumps({"issues": issues}, sort_keys=True),
        "faker-kit status:",
        *wake.lines,
        *([undelivered] if undelivered else []),
        *([cards] if cards else []),
        *refusals,
        *fresh_picks,
        *([wake.summary] if wake.summary else []),
        *wake.stuck,
    )


main()
