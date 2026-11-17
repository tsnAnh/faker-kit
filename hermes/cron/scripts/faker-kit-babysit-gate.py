import json
import time

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


def main() -> None:
    issues = listed_issues()
    lines = gate.labeled_lines(gate.collapse(gate.status_lines()))
    news = f"faker-kit issues lists {len(issues)} issue(s) to handle" if issues else None
    undelivered = chief_news.deliver(lines, time.time())
    news = news or undelivered
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
        *([wake.summary] if wake.summary else []),
        *wake.stuck,
    )


main()
