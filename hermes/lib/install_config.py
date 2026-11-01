from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import yaml


class InstallError(Exception):
    pass


def hermes(*args: str) -> str:
    done = subprocess.run(["hermes", *args], capture_output=True, text=True)
    if done.returncode:
        raise InstallError(f"hermes {' '.join(a for a in args[:4] if not a.startswith('-') or a == '-p')} failed")
    return done.stdout


def hermes_send(profile: str, target: str, message: str, timeout: float = 60.0) -> None:
    exe = shutil.which("hermes") or str(Path.home() / ".local" / "bin" / "hermes")
    try:
        done = subprocess.run([exe, "-p", profile, "send", "--to", target, "--quiet", message], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("hermes send did not finish") from None
    if done.returncode:
        raise InstallError(f"hermes send exited {done.returncode}")


def profile_home(root: Path, profile: str) -> Path:
    return root if profile == "default" else root / "profiles" / profile


def mapping(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text()) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        raise InstallError(f"cannot read configuration mapping: {path}") from None
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InstallError(f"expected configuration mapping: {path}")
    return value


def section(config: dict, *keys: str) -> dict:
    for key in keys:
        config = config.get(key, {})
        if not isinstance(config, dict):
            raise InstallError(f"expected mapping at {'.'.join(keys)}")
    return config


def leaves(config: dict, prefix: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], object]]:
    for key, value in config.items():
        path = (*prefix, str(key))
        if isinstance(value, dict) and value:
            yield from leaves(value, path)
        else:
            yield path, value


def value_at(config: object, keys: tuple[str, ...]) -> object:
    for key in keys:
        if not isinstance(config, dict) or key not in config:
            return None
        config = config[key]
    return config


def config_set(root: Path, profile: str, keys: tuple[str, ...], value: object) -> None:
    key = ".".join(keys)
    literal = value if isinstance(value, str) else json.dumps(value)
    result = subprocess.run(["hermes", "-p", profile, "config", "set", key, literal], capture_output=True, text=True)
    if result.returncode:
        raise InstallError(f"Hermes config set failed for {profile}: {key}; check CLI/schema compatibility")
    actual = value_at(mapping(profile_home(root, profile) / "config.yaml"), keys)
    if actual != value or type(actual) is not type(value):
        raise InstallError(f"Hermes config set did not persist {profile}: {key} with the expected type/value")


def apply_profile(root: Path, profile: str, snippet: Path) -> None:
    current = mapping(profile_home(root, profile) / "config.yaml")
    if section(current, "gateway").get("standalone") is True:
        raise InstallError(f"{profile} has gateway.standalone: true; migrate it to the shared host gateway first")
    updates = []
    for keys, value in leaves(mapping(snippet)):
        section(current, *keys[:-1])
        if keys == ("plugins", "enabled"):
            enabled = value_at(current, keys)
            if enabled is not None and not isinstance(enabled, list):
                raise InstallError(f"expected plugins.enabled list in {profile}")
            current_list = enabled or []
            value = current_list + [item for item in (value if isinstance(value, list) else []) if item not in current_list]
        updates.append((keys, value))
    for keys, value in updates:
        config_set(root, profile, keys, value)


def cron_jobs(text: str) -> dict[str, dict]:
    jobs: dict[str, dict] = {}
    current: dict[str, object] = {}
    for line in re.sub(r"\x1b\[[0-9;]*m", "", text).splitlines():
        head = re.match(r"^\s*(\S+)\s+\[(active|paused|enabled|disabled)\]", line)
        if head:
            current = {"id": head.group(1), "enabled": head.group(2) in ("active", "enabled")}
            continue
        name = re.match(r"^\s*Name:\s*(.*?)\s*$", line)
        if name and current:
            jobs[name.group(1)] = current
            continue
        row = re.match(r"^\s*(Schedule|Script|Mode):\s*(.*?)\s*$", line)
        if row and current:
            current[row.group(1).lower()] = row.group(2)
    return jobs


JOB_NAME = re.compile(r"\Afaker-kit-[a-z][a-z0-9-]*\Z")
KIT_GATE_SCRIPT = re.compile(r"\Afaker-kit-[a-z0-9-]+-gate\.py\Z")


def cron_spec(source: Path, bot: str) -> list[dict]:
    out = []
    for block in re.split(r"^## ", source.read_text(), flags=re.M)[1:]:
        name = block.split("\n", 1)[0].strip()
        if not JOB_NAME.match(name):
            raise InstallError(f"cron job name {name!r} must look like faker-kit-<word>")

        def field(key: str, optional: bool = False, block: str = block, name: str = name) -> str | None:
            match = re.search(rf"^{key}: `(.+)`[ \t]*$", block, re.M)
            if match is None:
                if optional:
                    return None
                raise InstallError(f"missing cron {key} field for {name}")
            return match.group(1).replace("{bot}", bot)

        script = field("Script", optional=True)
        if script is not None and not KIT_GATE_SCRIPT.match(script):
            raise InstallError(f"cron Script {script!r} for {name} must be a bare file name like faker-kit-<word>-gate.py")
        out.append({"name": name, "schedule": field("Schedule"), "deliver": field("Deliver"), "prompt": field("Prompt"), "skill": field("Skill", optional=True), "script": script})
    return out
