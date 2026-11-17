import json
import subprocess
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


def card_check_line() -> str | None:
    try:
        done = subprocess.run([gate.WRAPPER, "loop", "check"], env=gate.child_env(), capture_output=True, text=True, timeout=gate.TIMEOUT_SECONDS, check=False)
        report = json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        report = None
    problems = report.get("problems") if isinstance(report, dict) else None
    if not isinstance(problems, list):
        return "faker-kit loop check could not read the board this tick; run faker-kit loop check"
    if not problems:
        return None
    first = problems[0].get("line") if isinstance(problems[0], dict) else None
    return f"faker-kit loop check: {len(problems)} card(s) do not show the work, first: {first}; run faker-kit loop check for every fix line"


def main() -> None:
    issues = listed_issues()
    lines = gate.labeled_lines(gate.collapse(gate.status_lines()))
    news = f"faker-kit issues lists {len(issues)} issue(s) to handle" if issues else None
    undelivered = chief_news.deliver(lines, time.time())
    cards = card_check_line()
    news = news or undelivered or cards
    wake = gate.tick(JOB, lines, time.time(), news, summaries=True)
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
        *([wake.summary] if wake.summary else []),
    )


main()
