from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import yaml
from faker_kit_gh import Coded, scrub
from faker_kit_machine import PUBLIC_ROUTE_PATH, WEBHOOK_HOST, WEBHOOK_PORT

CODES = (
    "ok", "github_not_signed_in", "github_sign_in_expired", "github_unreachable", "kit_source_missing", "kit_fetch_failed", "kit_install_failed",
    "kit_behind", "update_in_progress", "gateway_unreachable", "routes_not_loaded", "public_path_down", "tunnel_config_unmanaged",
    "tunnel_restart_failed", "machine_config_invalid", "request_refused", "unexpected",
)
ACTION_LABELS = {
    "sign_in_github": "Sign in to GitHub for this machine",
    "update_kit": "Update the kit on this machine",
    "reload_routes": "Reload the project webhook routes",
    "reconnect_public_path": "Reconnect this machine's public address",
    "restart_gateway": "Restart the Hermes gateway",
    "contact_support": "Contact support",
}
GITHUB_CODES = {"not_signed_in": "github_not_signed_in", "sign_in_expired": "github_sign_in_expired", "unreachable": "github_unreachable"}
LONG_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,}")
SHORT_SECRET = 8
MESSAGE_CHARS = 240

TUNNEL_UNIT = "hermes-tunnel.service"
UNIT_NAME = re.compile(r"\A[A-Za-z0-9_.@-]+\.service\Z")
HOSTNAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9.-]*\Z")
PROJECT_RULE = re.compile(r"\A\^/p/[a-z0-9_-]+/webhooks/")
BACKUP_STAMP = "%Y%m%d-%H%M%S"
VALIDATE_SECONDS = 30
SERVICE_SECONDS = 60
LISTENER_SERVICE = f"http://{WEBHOOK_HOST}:{WEBHOOK_PORT}"
ARGV = re.compile(r"argv\[\]=([^;}]*)")

Tool = Callable[[list[str], int], tuple[int, str]]


def run_tool(argv: list[str], seconds: int) -> tuple[int, str]:
    env = dict(os.environ)
    runtime = f"/run/user/{os.getuid()}"
    if "XDG_RUNTIME_DIR" not in env and Path(runtime).is_dir():
        env["XDG_RUNTIME_DIR"] = runtime
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=seconds, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {seconds}s"
    except OSError as err:
        return 127, err.strerror or "could not run"
    return done.returncode, done.stdout + done.stderr


def clean(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in secrets:
        if len(secret) >= SHORT_SECRET:
            text = text.replace(secret, "[removed]")
    return LONG_TOKEN.sub("[removed]", scrub(text))[:MESSAGE_CHARS]


def envelope(code: str, message: str, data: dict | None = None, action: str | None = None, ok: bool | None = None) -> dict:
    if code not in CODES or (action is not None and action not in ACTION_LABELS):
        raise ValueError("untyped host result")
    return {"ok": code == "ok" if ok is None else ok, "code": code, "message": message,
            "next": {"action": action, "label": ACTION_LABELS[action]} if action else None, "data": data or {}}


def unmanaged(why: str) -> Coded:
    return Coded(f"The public tunnel setup is not one this kit manages, so nothing was changed ({why}).", "tunnel_config_unmanaged", "reconnect_public_path")


def item_starts(lines: list[str]) -> list[int]:
    start = next((i for i, line in enumerate(lines) if re.match(r"ingress:\s*(?:#.*)?$", line)), None)
    if start is None:
        raise unmanaged("no ingress list")
    indent = None
    found: list[int] = []
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        margin = line[:len(line) - len(line.lstrip())]
        if indent is None:
            indent = margin
        if not margin.startswith(indent) or len(margin) < len(indent):
            break
        if margin == indent and re.match(r"-(?:\s|$)", line[len(indent):]):
            found.append(i)
    return found


def plan_rule(text: str) -> str | None:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise unmanaged("not readable as YAML") from None
    rules = data.get("ingress") if isinstance(data, dict) else None
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        raise unmanaged("no ingress rules")
    routed = [i for i, r in enumerate(rules) if isinstance(r.get("path"), str) and PROJECT_RULE.match(r["path"])]
    if not routed:
        raise unmanaged("no per-project webhook rules")
    hosts = {rules[i].get("hostname") for i in routed}
    services = {rules[i].get("service") for i in routed}
    host = next(iter(hosts))
    if len(hosts) != 1 or not isinstance(host, str) or not HOSTNAME.match(host) or services != {LISTENER_SERVICE}:
        raise unmanaged("the project rules do not share one host and the local gateway")
    if any(r.get("hostname") == host and r.get("path") == PUBLIC_ROUTE_PATH and r.get("service") == LISTENER_SERVICE for r in rules):
        return None
    after = next((i for i, r in enumerate(rules) if i > routed[-1] and r.get("hostname") == host and "path" not in r), None)
    if after is None:
        raise unmanaged("no host rule without a path after the project rules")
    lines = text.splitlines(keepends=True)
    starts = item_starts(lines)
    if len(starts) != len(rules) or not lines[starts[after] - 1].endswith("\n"):
        raise unmanaged("the rule list could not be located line by line")
    indent = lines[starts[after]][:len(lines[starts[after]]) - len(lines[starts[after]].lstrip())]
    new = {"hostname": host, "path": PUBLIC_ROUTE_PATH, "service": LISTENER_SERVICE}
    block = f"{indent}- hostname: {host}\n{indent}  path: {PUBLIC_ROUTE_PATH}\n{indent}  service: {LISTENER_SERVICE}\n"
    updated = "".join([*lines[:starts[after]], block, *lines[starts[after]:]])
    try:
        again = yaml.safe_load(updated)
    except yaml.YAMLError:
        raise unmanaged("the new rule did not parse") from None
    if again != {**data, "ingress": [*rules[:after], new, *rules[after:]]}:
        raise unmanaged("the new rule did not land where planned")
    return updated


def write_exact(path: Path, text: str, mode: int, replace: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    fd = os.open(path, flags, mode)
    with os.fdopen(fd, "w") as out:
        os.fchmod(out.fileno(), mode)
        out.write(text)
        out.flush()
        os.fsync(out.fileno())


def validator_for(config: Path) -> Path | None:
    for candidate in sorted(config.parent.glob("cloudflared*")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def swap_in(config: Path, text: str, mode: int) -> None:
    staged = config.with_name(f".{config.name}.faker-kit-{os.getpid()}")
    try:
        write_exact(staged, text, mode, True)
        staged.replace(config)
    finally:
        staged.unlink(missing_ok=True)


def service_scope(unit: str, tool: Tool) -> tuple[bool, str] | None:
    for user in (True, False):
        code, out = tool(["systemctl", *(["--user"] if user else []), "show", unit, "-p", "LoadState", "-p", "ExecStart"], SERVICE_SECONDS)
        if code == 0 and "LoadState=loaded" in out:
            return user, out
    return None


def config_of(show: str) -> Path | None:
    for argv in ARGV.findall(show):
        try:
            words = shlex.split(argv)
        except ValueError:
            continue
        for i, word in enumerate(words):
            value = words[i + 1] if word == "--config" and i + 1 < len(words) else word.removeprefix("--config=") if word.startswith("--config=") else None
            if value and Path(value).is_absolute():
                return Path(value)
    return None


def reconcile_tunnel(config: Path | None, unit: str | None, now: float, tool: Tool) -> tuple[bool, str]:
    name = unit or TUNNEL_UNIT
    scope = service_scope(name, tool)
    if scope is None:
        if config is not None:
            raise unmanaged("no service on this machine runs the named tunnel")
        return False, "no live tunnel service was found on this machine, so there was nothing to reconcile"
    user, show = scope
    path = config or config_of(show)
    if path is None:
        raise unmanaged("the tunnel service does not name its config")
    try:
        original = path.read_text()
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as err:
        raise unmanaged(f"the tunnel config cannot be read: {err.strerror}") from None
    updated = plan_rule(original)
    if updated is None:
        return False, "the one-rule public path is already in the live tunnel config"
    checked = "the tunnel program's own check was skipped because it was not found beside the config"
    program = validator_for(path)
    staged = path.with_name(f".{path.name}.check-{os.getpid()}")
    try:
        write_exact(staged, updated, mode, True)
        if program is not None:
            code, _ = tool([str(program), "tunnel", "--config", str(staged), "ingress", "validate"], VALIDATE_SECONDS)
            if code != 0:
                raise unmanaged("the tunnel program rejected the new rule")
            checked = "checked with the tunnel program's own validation"
    finally:
        staged.unlink(missing_ok=True)
    backup = path.with_name(f"{path.name}.bak-{time.strftime(BACKUP_STAMP, time.gmtime(now))}")
    try:
        write_exact(backup, original, mode, False)
    except FileExistsError:
        if backup.read_text() != original:
            raise unmanaged("a backup with this timestamp already exists") from None
    swap_in(path, updated, mode)
    argv = ["systemctl", *(["--user"] if user else ["--no-ask-password"]), "restart", name]
    code, _ = tool(argv, SERVICE_SECONDS)
    if code != 0:
        swap_in(path, original, mode)
        again, _ = tool(argv, SERVICE_SECONDS)
        if again == 0:
            backup.unlink(missing_ok=True)
            raise Coded("The public tunnel could not be restarted with the new rule, so the previous setup was put back and the tunnel restarted on it.",
                        "tunnel_restart_failed", "reconnect_public_path")
        raise Coded("The public tunnel could not be restarted, even after the previous setup was put back, so it may be stopped.", "tunnel_restart_failed", "reconnect_public_path")
    return True, f"added the one-rule public path ahead of the pathless host rule, kept a timestamped backup, {checked}, and restarted the tunnel service once"
