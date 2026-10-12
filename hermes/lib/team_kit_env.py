"""A bot's own .env values, read by the bot's commands themselves.

Hermes passes a routed profile's .env to a command only for names declared in
terminal.env_passthrough, and never passes GH_TOKEN or GITHUB_TOKEN. Without this, team-kit-gh
and team-kit-chief run gh on the machine's own gh login, which can reach every repository the
owner can, and start claude without the config directory that holds its login and trusted folders.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

ENV_LINE = re.compile(r"\A([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z")
GITHUB_KEY = "GH_TOKEN"
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
    """Put the bot's own GitHub token and Claude config directory into this command's environment.

    The profile's token wins over any inherited one, and an inherited GITHUB_TOKEN is dropped, so gh acts
    only as the bot. CLAUDE_CONFIG_DIR is only filled in when missing.
    """
    values = parse_env_file(profile / ".env")
    if values.get(GITHUB_KEY):
        environ[GITHUB_KEY] = values[GITHUB_KEY]
        environ.pop("GITHUB_TOKEN", None)
    if values.get(CLAUDE_CONFIG_DIR) and not environ.get(CLAUDE_CONFIG_DIR):
        environ[CLAUDE_CONFIG_DIR] = values[CLAUDE_CONFIG_DIR]
