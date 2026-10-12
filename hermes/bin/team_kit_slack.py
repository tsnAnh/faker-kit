#!/usr/bin/env python3
"""team-kit-slack: outbound-only Slack Socket Mode relay for Hermes Bot Chat notices.

  check                         validate Slack relay config and token environment names
  run [--once]                  connect to Slack Socket Mode and relay configured app mentions
  mirror --file PATH|-          optionally copy an existing team-kit notice to Slack
  service --format systemd|launchd
                                print a user service unit for the relay

The relay never accepts inbound HTTP and never runs Slack text as a command. Slack mentions are
delivered to the configured bot's Bot Chat as `team-kit notice:` proposals with the Slack body
inside an <untrusted> wrapper.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from team_kit_guard.policy import PolicyError, load_policy  # noqa: E402
from team_kit_slack_relay import SlackError, SlackRelay, check, launchd_plist, mirror_notice, systemd_unit  # noqa: E402


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="team-kit-slack")
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")
    run.add_argument("--max-events", type=int)
    mirror = sub.add_parser("mirror")
    mirror.add_argument("--file", required=True)
    service = sub.add_parser("service")
    service.add_argument("--format", choices=("systemd", "launchd"), required=True)
    return top


def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text()
    except OSError as err:
        raise SlackError(f"cannot read notice file: {path}") from err


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        policy = load_policy()
        if args.command == "check":
            print(json.dumps(check(policy), ensure_ascii=False))
            return 0
        if args.command == "service":
            print(systemd_unit(policy) if args.format == "systemd" else launchd_plist(policy), end="")
            return 0
        if args.command == "mirror":
            print(json.dumps(mirror_notice(policy, read_text(args.file)), ensure_ascii=False))
            return 0
        max_events = 1 if args.once else args.max_events
        # The relay reconnects itself with backoff. A bounded run (--once, --max-events) gives up after a few tries.
        SlackRelay(policy).run(max_events=max_events, max_reconnects=3 if max_events is not None else None)
        return 0  # noqa: TRY300
    except (SlackError, PolicyError) as err:
        print(f"team-kit-slack: refused: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001 - never print token-bearing internals or tracebacks
        print(f"team-kit-slack: refused: unexpected {type(err).__name__}; relay stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
