import faker_kit_gate as gate

KIT_OWNED = True
QUIET_KEYS = ("new", "resolved", "unreadable")


def main() -> None:
    done = gate.run_kit("incidents")
    report = gate.parse_json(done, "incidents")
    if not isinstance(report, dict) or not all(isinstance(report.get(key), list) for key in QUIET_KEYS):
        gate.fail("faker-kit incidents printed an unexpected report shape", done.stderr)
    if all(report[key] == [] for key in QUIET_KEYS):
        gate.quiet()
        return
    ran_at = report.get("ran_at")
    if not isinstance(ran_at, str):
        gate.fail("faker-kit incidents printed no ran_at", done.stderr)
    gate.say(
        f"faker-kit incidents ran at {ran_at}.",
        f"New incidents: {len(report['new'])}",
        f"Resolved incidents: {len(report['resolved'])}",
        f"Unreadable sources: {len(report['unreadable'])}",
        f"Stop the line: {'true' if report.get('stop_the_line') is True else 'false'}",
        "Read this run's report with: faker-kit incidents --last",
    )


main()
