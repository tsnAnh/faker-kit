from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import run_dispatch_e2e as e2e

BOOTSTRAP_BOT = e2e.BOOTSTRAP
NEW_BOT = "crm-bot"
LATE_BOT = "late-bot"
CONTROL_BOT = "control-bot"
AGENT_TURN = {"HERMES_AGENT_TOOL_CONTEXT": "1"}
QUIET = {"PYTHONDONTWRITEBYTECODE": "1"}
ALLOWED_FORMS = ("hermes profile create {bot}", "hermes profile create --no-alias {bot}", "hermes profile create {bot} --no-alias")
DETECT = """
import json, sys
from tools.approval_detection import detect_human_required_command
print(json.dumps({command: list(detect_human_required_command(command)) for command in json.loads(sys.argv[1])}))
"""
MINT = """
import sys
from tools.approval_detection import detect_human_required_command
from hermes_cli.profile_admin_guard import mint_grant
command = sys.argv[1]
found, description = detect_human_required_command(command)
print(mint_grant(command, description or "") if found else "")
"""


def find_hermes(checkout: Path) -> tuple[Path, Path]:
    for folder in (".venv", "venv"):
        hermes, python = checkout / folder / "bin" / "hermes", checkout / folder / "bin" / "python"
        if hermes.is_file() and python.is_file():
            return hermes, python
    raise SystemExit(f"no .venv/bin/hermes in {checkout}")


def fingerprint(home: Path) -> dict:
    found: dict = {}
    local = home / ".local" / "bin"
    found["local-bin"] = sorted((p.name, p.lstat().st_mtime_ns) for p in local.iterdir()) if local.is_dir() else None
    hermes = home / ".hermes"
    found["hermes"] = sorted(p.name for p in hermes.iterdir()) if hermes.is_dir() else None
    for sub in ("profiles", "shared", "faker-kit"):
        folder = hermes / sub
        found[sub] = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else None
    for name in ("config.yaml", "SOUL.md"):
        path = hermes / name
        found[name] = (path.stat().st_size, path.stat().st_mtime_ns) if path.is_file() else None
    return found


def run_in_checkout(python: Path, checkout: Path, env: dict[str, str], code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(python), "-c", code, *args], cwd=checkout, env=env, capture_output=True, text=True, timeout=120)


def probe(run: e2e.Run, checkout: Path, hermes: Path, python: Path) -> None:
    shutil.rmtree(run.profile, ignore_errors=True)
    run.update_gh(lambda state: state.update({"repo": "acme/crm"}))
    shim = run.bin / "hermes"
    shim.write_text(f'#!/bin/sh\nexec "{hermes}" "$@"\n')
    shim.chmod(0o755)
    env = {**run.env(), **QUIET}
    home = run.home.resolve()
    crm = ("add-project", "acme/crm", "--bot", NEW_BOT, "--project", "crm")

    def cli(*argv: str, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["hermes", *argv], env={**env, **(extra or {})}, capture_output=True, text=True, timeout=300)

    def live(bot: str) -> bool:
        return cli("profile", "show", bot).returncode == 0

    version = cli("--version")
    run.step("the Hermes under test runs against a temporary home", version.returncode == 0 and str(home) != str(Path.home().resolve()),
             (version.stdout.strip().splitlines() or ["no version"])[0])

    denied = cli("-p", "default", "profile", "create", NEW_BOT, "--no-alias", extra=AGENT_TURN)
    run.step("Hermes denies profile create from an agent's command and creates nothing",
             denied.returncode != 0 and "needs your approval" in denied.stderr and not live(NEW_BOT), denied.stderr.strip())

    policy = e2e.load_policy(run.team)
    commands = [form.format(bot=NEW_BOT) for form in ALLOWED_FORMS]
    classified = run_in_checkout(python, checkout, env, DETECT, json.dumps(commands))
    verdicts = json.loads(classified.stdout) if classified.returncode == 0 else {}
    guard_allows = {c: e2e.decide(policy, BOOTSTRAP_BOT, "terminal", {"command": c}, False) is None for c in commands}
    guard_refuses = {role: all(e2e.decide(policy, bot, "terminal", {"command": c}, False) is not None for c in commands) for role, bot in (("project", e2e.BOT),)}
    run.step("every form the guard allows the bootstrap bot is one Hermes's own detection classifies as needing a human",
             classified.returncode == 0 and all(guard_allows.values()) and all(verdicts[c][0] is True for c in commands) and all(guard_refuses.values()),
             json.dumps({c: verdicts.get(c) for c in commands}) or classified.stderr.strip()[-200:])

    command = f"hermes profile create {NEW_BOT}"
    minted = run_in_checkout(python, checkout, env, MINT, command)
    token = minted.stdout.strip()
    created = cli("profile", "create", NEW_BOT, extra={**AGENT_TURN, "HERMES_PROFILE_ADMIN_GRANT": token})
    replay = cli("profile", "create", LATE_BOT, extra={**AGENT_TURN, "HERMES_PROFILE_ADMIN_GRANT": token})
    wrapper = home / ".local" / "bin" / NEW_BOT
    run.step("with a grant Hermes minted for the approved command, profile create succeeds once, writes its wrapper under the temporary home, and the grant cannot be replayed",
             bool(token) and created.returncode == 0 and live(NEW_BOT) and wrapper.exists() and replay.returncode != 0 and not live(LATE_BOT),
             (created.stderr + replay.stderr).strip()[:200] or "created, replay denied")

    migrated = cli("catalog", "migrate")
    marker = run.hermes / "shared" / "catalog" / ".migrated"
    cli("profile", "create", CONTROL_BOT, "--no-alias")
    control_skills = run.hermes / "profiles" / CONTROL_BOT / "skills"
    control_skills.mkdir(parents=True, exist_ok=True)
    (control_skills / "dispatch").symlink_to(run.team / "kit" / "hermes" / "skills" / "dispatch")
    control = cli("-p", CONTROL_BOT, "skills", "list")
    run.step("with the shared skill catalog active, a skill symlinked into a profile's skills folder is not listed (the cause)",
             migrated.returncode == 0 and marker.is_file() and control.returncode == 0 and "dispatch" not in control.stdout, control.stdout.strip()[-120:])

    other_env = {**AGENT_TURN, **QUIET}
    code, out, err = run.kit(*crm, bot=BOOTSTRAP_BOT, env=other_env)
    added = json.loads(out) if code == 0 else {}
    run.step("add-project in an agent's turn completes against real Hermes once the profile exists, with jobs running",
             code == 0 and added.get("jobs_active") is True and len(added.get("jobs_created", [])) >= 3 and live(NEW_BOT), err.strip() or out[:200])

    listing = cli("-p", NEW_BOT, "skills", "list")
    row = next((line for line in listing.stdout.splitlines() if "dispatch" in line), "")
    run.step("hermes -p <bot> skills list shows the bot's dispatch skill enabled, with the catalog active", listing.returncode == 0 and "enabled" in row and "bootstrap" not in listing.stdout
             and "chief" not in listing.stdout, " ".join(row.split()))

    again = run.kit(*crm, bot=BOOTSTRAP_BOT, env=other_env)
    listing_again = cli("-p", NEW_BOT, "skills", "list")
    run.step("running add-project again changes nothing about the skill", again[0] == 0 and listing_again.stdout == listing.stdout, again[2].strip())

    labels = run.gh_now()["labels"]
    bots_before = (run.team / "bots.yaml").read_text()
    code, out, err = run.kit("add-project", "acme/late", "--bot", LATE_BOT, "--project", "late", bot=BOOTSTRAP_BOT, env=other_env)
    run.step("without an approval, add-project for a missing profile refuses with Hermes's reason and what to run, and writes nothing",
             code == 1 and "needs your approval" in err and f"Run `hermes profile create {LATE_BOT}` yourself" in err and (run.team / "bots.yaml").read_text() == bots_before
             and run.gh_now()["labels"] == labels and not live(LATE_BOT) and LATE_BOT not in bots_before, err.strip()[:300])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real-Hermes probe of the profile approval path for faker-kit add-project")
    parser.add_argument("--hermes-checkout", required=True, type=Path)
    parser.add_argument("--artifact", type=Path, help="where to write the JSON transcript; defaults to a file in the run directory")
    parser.add_argument("--keep", action="store_true", help="keep the run directory")
    args = parser.parse_args(argv)
    checkout = args.hermes_checkout.resolve()
    hermes, python = find_hermes(checkout)
    real_home = Path.home().resolve()
    for key in [k for k in os.environ if k.startswith(("HERMES_", "FAKER_KIT", "XDG_")) or k == "CLAUDE_CONFIG_DIR"]:
        del os.environ[key]
    before = fingerprint(real_home)
    root = Path(tempfile.mkdtemp(prefix="faker-kit-live-"))
    run = e2e.Run(root)
    code = 0
    try:
        run.build()
        probe(run, checkout, hermes, python)
        after = fingerprint(real_home)
        run.step("nothing leaked into the real ~/.hermes or ~/.local/bin", before == after and (run.home / ".local" / "bin").is_dir(),
                 "the real folders list the same entries as before; the wrapper went to the temporary home")
    except e2e.Failure as err:
        print(f"probe stopped at: {err}", file=sys.stderr)
        code = 1
    proof = {"steps": [[s["step"], s["ok"]] for s in run.steps]}
    transcript = {"ok": code == 0, "hermes_checkout": str(checkout), "steps": run.steps, "digest": hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()}
    artifact = args.artifact or root / "profile-approval-live.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps(transcript, indent=2) + "\n")
    print(f"transcript: {artifact} sha256={transcript['digest']}")
    if not args.keep and args.artifact:
        shutil.rmtree(root, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
