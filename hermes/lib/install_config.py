"""Hermes configuration adapter for install.sh and team-kit-admin: read YAML, write through the Hermes CLI.

Every ordinary write goes through `hermes ... config set` and is read back, so a CLI that ignores
or coerces a value is caught. A value that holds a secret, such as the webhook routes, never goes
on a command line, where `ps` would show it: config_set_private writes that one key into the YAML
file in place, atomically, keeping every other key and the file's mode. Error messages name the
key, never the value.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


class InstallError(Exception):
    pass


def hermes(*args: str) -> str:
    done = subprocess.run(["hermes", *args], capture_output=True, text=True)
    if done.returncode:
        raise InstallError(f"hermes {' '.join(a for a in args[:4] if not a.startswith('-') or a == '-p')} failed")
    return done.stdout


def profile_home(root: Path, profile: str) -> Path:
    return root if profile == "default" else root / "profiles" / profile


def mapping(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text()) if path.exists() else {}
    except (OSError, yaml.YAMLError):
        # A parser exception may contain a secret-bearing source line.
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


def leaves(config: dict, prefix=()):
    for key, value in config.items():
        path = prefix + (key,)
        if isinstance(value, dict) and value:
            yield from leaves(value, path)
        else:
            yield path, value


def value_at(config, keys):
    for key in keys:
        if not isinstance(config, dict) or key not in config:
            return None
        config = config[key]
    return config


def config_set(root: Path, profile: str, keys, value) -> None:
    key = ".".join(keys)
    # Hermes preserves string scalars; JSON literals retain list/map/bool typing.
    literal = value if isinstance(value, str) else json.dumps(value)
    result = subprocess.run(["hermes", "-p", profile, "config", "set", key, literal], capture_output=True, text=True)
    if result.returncode:
        # CLI output can echo the value, including a route's secret.
        raise InstallError(f"Hermes config set failed for {profile}: {key}; check CLI/schema compatibility")
    actual = value_at(mapping(profile_home(root, profile) / "config.yaml"), keys)
    if actual != value or type(actual) is not type(value):
        raise InstallError(f"Hermes config set did not persist {profile}: {key} with the expected type/value")


def config_set_private(root: Path, profile: str, keys, value) -> None:
    path = profile_home(root, profile) / "config.yaml"
    config = mapping(path)
    node = config
    for key in keys[:-1]:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            raise InstallError(f"expected mapping at {'.'.join(keys)} in {profile}")
        node = child
    node[keys[-1]] = value
    tmp = path.with_name(f".{path.name}.team-kit.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        yaml.safe_dump(config, out, sort_keys=False, allow_unicode=True)
    tmp.replace(path)
    if value_at(mapping(path), keys) != value:
        raise InstallError(f"could not write {profile}: {'.'.join(keys)}")


def apply_profile(root: Path, profile: str, snippet: Path) -> None:
    current = mapping(profile_home(root, profile) / "config.yaml")
    if section(current, "gateway").get("standalone") is True:
        raise InstallError(f"{profile} has gateway.standalone: true; migrate it to the shared host gateway first")
    updates = []
    for keys, value in leaves(mapping(snippet)):
        # Validate all parent mappings before writing any setting.
        section(current, *keys[:-1])
        if keys == ("plugins", "enabled"):
            enabled = value_at(current, keys)
            if enabled is not None and not isinstance(enabled, list):
                raise InstallError(f"expected plugins.enabled list in {profile}")
            value = (enabled or []) + [item for item in value if item not in (enabled or [])]
        updates.append((keys, value))
    for keys, value in updates:
        config_set(root, profile, keys, value)


def webhook_updates(root: Path, name: str, route: dict, port: int = 8644) -> list[tuple[tuple[str, ...], Any]]:
    """The host config writes that add route `name`. Refuses to shadow a route someone else owns.

    The routes value holds every route's secret, so write it with config_set_private."""
    host = mapping(root / "config.yaml")
    webhook = section(host, "platforms", "webhook")
    if name in mapping(root / "webhook_subscriptions.json"):
        raise InstallError(f"conflicting dynamic webhook route {name}; left unchanged")
    if not route.get("secret"):
        raise InstallError("webhook-secret is empty; supply a nonempty HMAC secret before installing")
    extra = section(webhook, "extra")
    # Preserve both the direct spelling and the extra spelling accepted by Hermes.
    # Extra takes precedence; do not silently shadow a conflicting owned route.
    direct_routes, extra_routes = section(webhook, "routes"), section(extra, "routes")
    for routes in (direct_routes, extra_routes):
        existing = routes.get(name)
        if existing is not None and (not isinstance(existing, dict) or any(
                existing.get(key) != route[key] for key in ("profile", "script", "cron_job", "secret"))):
            raise InstallError(f"conflicting host webhook route {name}; left unchanged")
    routes = {**direct_routes, **extra_routes, name: route}
    updates: list[tuple[tuple[str, ...], Any]] = [(("platforms", "webhook", "enabled"), True)]
    if "port" not in extra and "port" not in webhook:
        updates.append((("platforms", "webhook", "extra", "port"), port))
    updates.append((("platforms", "webhook", "extra", "routes"), routes))
    return updates


def cron_jobs(text: str) -> dict[str, dict]:
    """`hermes cron list --all` output to {name: {id, enabled}}. The CLI prints an id line, then a Name row."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    jobs: dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        head = re.match(r"^\s*(\S+)\s+\[(active|paused|enabled|disabled)\]", line)
        if head:
            current = {"id": head.group(1), "enabled": head.group(2) in ("active", "enabled")}
            continue
        name = re.match(r"^\s*Name:\s*(.*?)\s*$", line)
        if name and current is not None:
            jobs[name.group(1)] = current
            current = None
    return jobs


def cron_spec(source: Path, bot: str) -> list[dict]:
    """The jobs in a cron spec file: each `##` heading is a job, with Schedule, Deliver, Skill, and a text prompt."""
    out = []
    for block in re.split(r"^## ", source.read_text(), flags=re.M)[1:]:
        name = block.split("\n", 1)[0].strip()

        def field(key):
            match = re.search(rf"^{key}: `([^`]+)`", block, re.M)
            if match is None:
                raise InstallError(f"missing cron {key} field for {name}")
            return match.group(1).replace("{bot}", bot)

        match = re.search(r"```text\n(.*?)```", block, re.S)
        if match is None:
            raise InstallError(f"missing cron prompt for {name}")
        out.append({"name": name, "schedule": field("Schedule"), "deliver": field("Deliver"), "skill": field("Skill"), "prompt": match.group(1).strip()})
    return out
