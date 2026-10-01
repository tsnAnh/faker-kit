"""Installer configuration adapter: read YAML, write only through Hermes CLI."""
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


class InstallError(Exception):
    pass


def mapping(path):
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


def section(config, *keys):
    for key in keys:
        config = config.get(key, {})
        if not isinstance(config, dict):
            raise InstallError(f"expected mapping at {'.'.join(keys)}")
    return config


def leaves(config, prefix=()):
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


def config_set(root, profile, keys, value):
    key = '.'.join(keys)
    # Hermes preserves string scalars; JSON literals retain list/map/bool typing.
    literal = value if isinstance(value, str) else json.dumps(value)
    result = subprocess.run(["hermes", "-p", profile, "config", "set", key, literal],
                            capture_output=True, text=True)
    if result.returncode:
        # CLI output can echo the value, including a route's secret.
        raise InstallError(f"Hermes config set failed for {profile}: {key}; check CLI/schema compatibility")
    home = root if profile == "default" else root / "profiles" / profile
    actual = value_at(mapping(home / "config.yaml"), keys)
    if actual != value or type(actual) is not type(value):
        raise InstallError(f"Hermes config set did not persist {profile}: {key} with the expected type/value")


def apply_profile(root, profile, snippet):
    current = mapping(root / "profiles" / profile / "config.yaml")
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


def webhook_plan(root, snippet):
    host = mapping(root / "config.yaml")
    dispatcher = mapping(root / "profiles" / "dispatcher" / "config.yaml")
    if section(dispatcher, "gateway").get("standalone") is True:
        raise InstallError("dispatcher has gateway.standalone: true; migrate to the shared host gateway before installing")
    webhook = section(host, "platforms", "webhook")
    if "team-kit-github" in mapping(root / "webhook_subscriptions.json"):
        raise InstallError("conflicting dynamic webhook route team-kit-github; left unchanged")
    extra = section(webhook, "extra")
    supplied = section(mapping(snippet), "platforms", "webhook", "extra")
    route = dict(section(supplied, "routes")["team-kit-github"])
    secret_path = root / "team-kit" / "webhook-secret"
    try:
        route["secret"] = secret_path.read_text().strip()
    except OSError:
        raise InstallError(f"cannot read webhook-secret: {secret_path}") from None
    if not route["secret"]:
        raise InstallError("webhook-secret is empty; supply a nonempty HMAC secret before installing")
    route["profile"] = "dispatcher"
    # Preserve both the direct spelling and the extra spelling accepted by Hermes.
    # Extra takes precedence; do not silently shadow a conflicting owned route.
    direct_routes, extra_routes = section(webhook, "routes"), section(extra, "routes")
    for routes in (direct_routes, extra_routes):
        existing = routes.get("team-kit-github")
        if existing is not None and (not isinstance(existing, dict) or any(
                existing.get(key) != route[key] for key in ("profile", "script", "cron_job", "secret"))):
            raise InstallError("conflicting host webhook route team-kit-github; left unchanged")
    routes = {**direct_routes, **extra_routes, "team-kit-github": route}
    updates: list[tuple[tuple[str, ...], Any]] = [(('platforms', 'webhook', 'enabled'), True)]
    if "port" not in extra and "port" not in webhook:
        updates.append((('platforms', 'webhook', 'extra', 'port'), supplied["port"]))
    updates.append((('platforms', 'webhook', 'extra', 'routes'), routes))
    return updates


def cron_names():
    # Current CLI prints a labeled Name row, with optional ANSI colour.
    text = re.sub(r"\x1b\[[0-9;]*m", "", sys.stdin.read())
    for name in re.findall(r"^\s*Name:\s*(.*?)\s*$", text, re.M):
        print(name)


def cron_spec(source, output):
    for block in re.split(r"^## ", source.read_text(), flags=re.M)[1:]:
        name = block.split("\n", 1)[0].strip()
        def field(key):
            match = re.search(rf"^{key}: `([^`]+)`", block, re.M)
            if match is None:
                raise InstallError(f"missing cron {key} field for {name}")
            return match.group(1)

        match = re.search(r"```text\n(.*?)```", block, re.S)
        if match is None:
            raise InstallError(f"missing cron prompt for {name}")
        prompt = match.group(1).strip()
        prompt_file = output / f".cron-{name}.txt"
        prompt_file.write_text(prompt)
        print("\t".join([name, field("Schedule"), field("Deliver"), field("Skill"), str(prompt_file)]))


def main():
    action, *args = sys.argv[1:]
    if action == "profile":
        root, profile, snippet = args
        apply_profile(Path(root), profile, Path(snippet))
    elif action in ("webhook-check", "webhook"):
        root, snippet = map(Path, args)
        updates = webhook_plan(root, snippet)
        if action == "webhook":
            for keys, value in updates:
                config_set(root, "default", keys, value)
    elif action == "cron-names":
        cron_names()
    elif action == "cron-spec":
        cron_spec(*map(Path, args))
    else:
        raise InstallError("unknown installer configuration action")


if __name__ == "__main__":
    try:
        main()
    except InstallError as exc:
        print(f"install.sh: {exc}", file=sys.stderr)
        sys.exit(1)
