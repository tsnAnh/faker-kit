import json
import subprocess

import faker_kit_gate as gate

KIT_OWNED = True
PART_KEYS = ("id", "project", "state", "prs", "waits_on")


def valid_part(part: object) -> bool:
    return isinstance(part, dict) and all(key in part for key in PART_KEYS) and isinstance(part["prs"], list)


def read_report(done: subprocess.CompletedProcess[str]) -> tuple[list[dict], list[dict]]:
    report = gate.parse_json(done, "loop --all")
    parts = report.get("open") if isinstance(report, dict) else None
    to_report = report.get("to_report") if isinstance(report, dict) else None
    if not isinstance(parts, list) or not isinstance(to_report, list) or not all(valid_part(part) for part in [*parts, *to_report]):
        gate.fail("faker-kit loop --all printed an unexpected report shape", done.stderr)
    return parts, to_report


def distinct(parts: list[dict]) -> list[dict]:
    ids: set[object] = set()
    pulls: set[tuple[str, object]] = set()
    kept = []
    for part in parts:
        claimed = {(str(part["project"]), pr) for pr in part["prs"]}
        if part["id"] in ids or (claimed and claimed <= pulls):
            continue
        ids.add(part["id"])
        pulls |= claimed
        kept.append(part)
    return kept


def due_waiting(parts: list[dict]) -> list[dict]:
    merged = {(part["project"], pr) for part in parts if part["state"] == "merged" for pr in part["prs"]}
    due = []
    for part in parts:
        project, _, number = str(part["waits_on"] or "").rpartition("#")
        if part["state"] == "waiting" and number.isdecimal() and (project, int(number)) in merged:
            due.append(part)
    return due


def main() -> None:
    parts, to_report = read_report(gate.run_kit("loop", "--all"))
    due, to_report = distinct(due_waiting(parts)), distinct(to_report)
    if not due and not to_report:
        gate.quiet()
        return
    gate.say(
        "The loop gate woke this tick because a waiting part is due or a part has news for the owner.",
        "faker-kit loop:",
        json.dumps({"due_waiting": due, "to_report": to_report}, sort_keys=True),
    )


main()
