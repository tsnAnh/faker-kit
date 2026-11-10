import time

import faker_kit_gate as gate

KIT_OWNED = True
JOB = "faker-kit-babysit"


def main() -> None:
    lines = gate.status_lines()
    reason = gate.tick(JOB, lines, time.time())
    if reason is None:
        gate.quiet()
        return
    gate.say(f"The babysit gate woke this tick because {reason}.", "faker-kit status:", *lines)


main()
