"""Hermetic CLI contract double; this is not a real Hermes integration."""
import json
import os
import sys
from pathlib import Path

import yaml

# Like Hermes, honor HERMES_HOME (a profile's own directory counts as its root's child).
_home = os.environ.get("HERMES_HOME")
root = (Path(_home).parent.parent if Path(_home).parent.name == "profiles" else Path(_home)) if _home else Path(os.environ["HOME"]) / ".hermes"
args = sys.argv[1:]
with (root / "calls.log").open("a") as log:
    log.write(json.dumps(args) + "\n")
profile = "default"
if args[:1] == ["-p"]:
    profile, args = args[1], args[2:]
home = root if profile == "default" else root / "profiles" / profile
if args[:2] == ["profile", "create"]:
    if os.environ.get("FAKE_PROFILE_CREATE_FAIL"):
        sys.exit(1)
    (root / "profiles" / args[2]).mkdir(parents=True)
elif args[:2] == ["config", "set"]:
    key, value = args[2:4]
    if os.environ.get("FAKE_CONFIG_FAIL") == key:
        print("failed setting " + value, file=sys.stderr)
        sys.exit(1)
    if os.environ.get("FAKE_CONFIG_IGNORE") == key:
        sys.exit(0)
    config_path = home / "config.yaml"
    config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    config = config or {}
    # Current Hermes preserves string scalar values, coerces booleans/numbers,
    # and parses list/mapping literals. Our snippets use only these types.
    if value.startswith(("[", "{")) or value in ("true", "false", "null") or value.isdecimal():
        value = yaml.safe_load(value)
    node = config
    parts = key.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
elif args[:2] == ["config", "unset"]:
    config_path = home / "config.yaml"
    config = (yaml.safe_load(config_path.read_text()) if config_path.exists() else {}) or {}
    node, parts = config, args[2].split(".")
    for part in parts[:-1]:
        node = node.get(part, {})
    node.pop(parts[-1], None)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
elif args[:2] == ["plugins", "install"]:
    # Like Hermes, a catalog name installs the plugin under its own key, in this profile's plugins/.
    if os.environ.get("FAKE_PLUGIN_INSTALL_FAIL"):
        sys.exit(1)
    key = {"claude-subscription-directsdk": "claude-subscription-directsdk-experimental"}.get(args[2], args[2])
    (home / "plugins" / key).mkdir(parents=True, exist_ok=True)
elif args[:2] == ["plugins", "enable"]:
    if not (home / "plugins" / args[2]).is_dir():
        print(f"No plugin named '{args[2]}'", file=sys.stderr)
        sys.exit(1)
    config_path = home / "config.yaml"
    config = (yaml.safe_load(config_path.read_text()) if config_path.exists() else {}) or {}
    enabled = config.setdefault("plugins", {}).setdefault("enabled", [])
    if args[2] not in enabled:
        enabled.append(args[2])
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
elif args[:1] == ["cron"]:
    jobs_path = home / "jobs.json"
    jobs = json.loads(jobs_path.read_text()) if jobs_path.exists() else []
    if args[1] == "list":
        if os.environ.get("FAKE_CRON_LIST_FAIL"):
            sys.exit(1)
        for job in jobs:
            print(f"  {job['id']} [{'active' if job['enabled'] else 'paused'}]")
            print(f"    Name:      {job['name']}")
            print(f"    Schedule:  {job.get('schedule', '')}")
    elif args[1] == "create":
        if "--paused" not in args:
            # Deliberately model the unsafe legacy default as active.
            enabled = True
        else:
            enabled = False
        used = [int(job["id"].removeprefix("job-")) for job in jobs if job["id"].startswith("job-") and job["id"].removeprefix("job-").isdigit()]
        jobs.append({"id": f"job-{max(used, default=-1) + 1}", "name": args[args.index("--name") + 1],
                     "schedule": args[2], "prompt": args[3],
                     "deliver": args[args.index("--deliver") + 1], "skill": args[args.index("--skill") + 1],
                     "enabled": enabled, "state": "active" if enabled else "paused",
                     "paused_reason": args[args.index("--paused-reason") + 1] if "--paused-reason" in args else None})
        jobs_path.write_text(json.dumps(jobs))
    elif args[1] == "edit":
        job = next((j for j in jobs if j["id"] == args[2]), None)
        if job is None:
            print("no such job", file=sys.stderr)
            sys.exit(1)
        # Like Hermes, edit changes configuration only; pause state is separate.
        if "--schedule" in args:
            job["schedule"] = args[args.index("--schedule") + 1]
        jobs_path.write_text(json.dumps(jobs))
    elif args[1] == "delete":
        jobs = [job for job in jobs if job["id"] != args[2]]
        jobs_path.write_text(json.dumps(jobs))
    elif args[1] == "resume":
        job = next((j for j in jobs if j["id"] == args[2]), None)
        if job is None:
            print("no such job", file=sys.stderr)
            sys.exit(1)
        job["enabled"], job["state"] = True, "active"
        jobs_path.write_text(json.dumps(jobs))
elif args[:1] == ["gateway"]:
    if profile != "default":
        print("profile is served by the shared host gateway", file=sys.stderr)
        sys.exit(1)
    state = home / "gateway-test.json"
    data = json.loads(state.read_text()) if state.exists() else {}
    data[args[1]] = True
    state.write_text(json.dumps(data))
else:
    print("unsupported fake CLI command", file=sys.stderr)
    sys.exit(2)
