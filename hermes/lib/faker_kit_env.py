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
    values = parse_env_file(profile / ".env")
    if values.get(CLAUDE_CONFIG_DIR) and not environ.get(CLAUDE_CONFIG_DIR):
        environ[CLAUDE_CONFIG_DIR] = values[CLAUDE_CONFIG_DIR]


def set_env(home: Path, key: str, value: str) -> None:
    env = home / ".env"
    kept = [line for line in (env.read_text().splitlines() if env.exists() else []) if not line.startswith(f"{key}=")]
    if value:
        kept.append(f"{key}={value}")
    if not kept and not env.exists():
        return
    tmp = env.with_name(f".env.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write("".join(f"{line}\n" for line in kept))
    tmp.replace(env)
