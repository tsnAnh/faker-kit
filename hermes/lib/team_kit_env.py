"""A bot's own .env values, read by the bot's commands themselves.

Hermes passes a routed profile's .env to a command only for names declared in
terminal.env_passthrough. Without this, team-kit-gh starts claude without the config directory
that holds its login and trusted folders, so the launch stops at the folder-trust question.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

ENV_LINE = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z")
CLAUDE_CONFIG_DIR = "CLAUDE_CONFIG_DIR"


class EnvFileError(ValueError):
    pass


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; blank lines, comments, and anything else are skipped. A missing file is empty."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return {}
    except OSError as err:
        raise EnvFileError(f"cannot read profile environment file: {path}") from err
    env: dict[str, str] = {}
    for raw in lines:
        line = raw.strip()
        match = ENV_LINE.match(line) if line and not line.startswith("#") else None
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def use_bot_env(profile: Path, environ: MutableMapping[str, str] = os.environ) -> None:
    """Fill in the bot's Claude config directory from its profile .env when this command's environment lacks it."""
    values = parse_env_file(profile / ".env")
    if values.get(CLAUDE_CONFIG_DIR) and not environ.get(CLAUDE_CONFIG_DIR):
        environ[CLAUDE_CONFIG_DIR] = values[CLAUDE_CONFIG_DIR]
