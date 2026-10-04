#!/usr/bin/env python3
"""Hermes webhook script for one project bot's team-kit route.

team-kit-admin add-project copies this file into the bot's scripts/team-kit/ and replaces the
bot name below, so the route can only ever wake that bot's drain. The logic is in
team_kit_triage.py, copied next to it.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from team_kit_triage import main  # noqa: E402

BOT = "@TEAM_KIT_BOT@"

if __name__ == "__main__":
    sys.exit(main(BOT))
