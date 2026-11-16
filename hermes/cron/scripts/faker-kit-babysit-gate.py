import json
import time

import faker_kit_gate as gate

KIT_OWNED = True
JOB = "faker-kit-babysit"


def listed_issues() -> list[object]:
    done = gate.run_kit("issues")
    report = gate.parse_json(done, "issues")
    issues = report.get("issues") if isinstance(report, dict) else None
    if not isinstance(issues, list):
        gate.fail("faker-kit issues printed an unexpected report shape", done.stderr)
    return issues


def main() -> None:
    issues = listed_issues()
    lines = gate.labeled_lines(gate.status_lines())
    news = f"faker-kit issues lists {len(issues)} issue(s) to handle" if issues else None
    reason = gate.tick(JOB, lines, time.time(), news)
    if reason is None:
        gate.quiet()
        return
    gate.say(
        f"The babysit gate woke this tick because {reason}.",
        "faker-kit issues:",
        json.dumps({"issues": issues}, sort_keys=True),
        "faker-kit status:",
        *lines,
    )


main()
