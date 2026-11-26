from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import yaml


class InstallError(Exception):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


def hermes(*args: str, timeout: float | None = None) -> str:
    label = " ".join(a for a in args[:4] if not a.startswith("-") or a == "-p")
    try:
        done = subprocess.run(["hermes", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise InstallError(f"hermes {label} did not answer within {timeout:g}s") from None
    except OSError:
        raise InstallError(f"hermes {label} could not run: the hermes executable was not found") from None
    if done.returncode:
        raise InstallError(f"hermes {label} failed", f"{done.stdout}\n{done.stderr}")
    return done.stdout


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


def holds(path: Path, keys: tuple[str, ...], value: object) -> bool:
    actual = value_at(mapping(path), keys)
    return actual == value and type(actual) is type(value)


def config_set(root: Path, profile: str, keys: tuple[str, ...], value: object) -> None:
    key = ".".join(keys)
    path = profile_home(root, profile) / "config.yaml"
    if value is not None and holds(path, keys, value):
        return
    literal = json.dumps(value)
    result = subprocess.run(["hermes", "-p", profile, "config", "set", key, literal], capture_output=True, text=True)
    if result.returncode:
        raise InstallError(f"Hermes config set failed for {profile}: {key}; check CLI/schema compatibility")
    if not holds(path, keys, value):
        raise InstallError(f"Hermes config set did not persist {profile}: {key} with the expected type/value")


def apply_profile(root: Path, profile: str, snippet: Path, overrides: dict[tuple[str, ...], object] | None = None) -> None:
    current = mapping(profile_home(root, profile) / "config.yaml")
    if section(current, "gateway").get("standalone") is True:
        raise InstallError(f"{profile} has gateway.standalone: true; migrate it to the shared host gateway first")
    updates = []
    for keys, value in {**dict(leaves(mapping(snippet))), **(overrides or {})}.items():
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


JOB_NAME = re.compile(r"\Afaker-kit-[a-z][a-z0-9-]*\Z")
KIT_GATE_SCRIPT = re.compile(r"\Afaker-kit-[a-z0-9-]+-gate\.py\Z")


def cron_spec(source: Path, bot: str, chief: str | None = None) -> list[dict]:
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
            return match.group(1).replace("{bot}", bot).replace("{chief}", chief or "")

        script = field("Script", optional=True)
        if script is not None and not KIT_GATE_SCRIPT.match(script):
            raise InstallError(f"cron Script {script!r} for {name} must be a bare file name like faker-kit-<word>-gate.py")
        to_chief = "{chief}" in block
        if to_chief and chief is None:
            continue
        out.append({"name": name, "schedule": field("Schedule"), "deliver": field("Deliver"), "prompt": field("Prompt"), "skill": field("Skill", optional=True), "script": script,
                    "no_agent": field("Mode", optional=True) == "no-agent", "chief": to_chief})
    return out
