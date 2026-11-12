from __future__ import annotations

import fcntl
import json
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

import yaml

Step = Callable[[str, bool, str], None]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(source: Path, path: str, text: str, message: str) -> str:
    target = source / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(source, "add", ".")
    git(source, "commit", "-qm", message)
    return git(source, "rev-parse", "HEAD")


def refresh_scenario(work: Path, step: Step, base_env: dict[str, str], fake_hermes: Path, make_source: Callable[[Path], tuple[Path, str]]) -> None:
    home = work / "home"
    hermes_home = home / ".hermes"
    state = hermes_home / "faker-kit"
    source, first = make_source(work)
    git(source, "branch", "-M", "main")
    tools = work / "tools"
    tools.mkdir()
    shutil.copy2(fake_hermes, tools / "hermes")
    env = {**base_env, "HOME": str(home), "FAKER_KIT_HOME": str(state), "PATH": f"{tools}:{base_env['PATH']}"}
    env.pop("HERMES_HOME", None)
    calls = hermes_home / "hermes-calls.log"
    wrapper = hermes_home / "bin" / "faker-kit"

    def install(ref: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(source / "hermes" / "install.sh"), "--ref", ref, "--source", str(source), "--no-gateway"],
                              env=env, capture_output=True, text=True, timeout=300)

    def refresh(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(wrapper), "refresh"], env={**env, **(extra_env or {})}, capture_output=True, text=True, timeout=600)

    def installed() -> str:
        return (state / "installed-ref").read_text().strip()

    def head() -> str:
        return git(state / "kit", "rev-parse", "HEAD")

    def hermes_calls() -> int:
        return len(calls.read_text().splitlines()) if calls.exists() else 0

    done = install(first)
    step("install.sh records the kit source and the installed commit, and registers the kit as a Hermes post-update step",
         done.returncode == 0 and (state / "kit-source").read_text().strip() == str(source) and installed() == first
         and yaml.safe_load((hermes_home / "config.yaml").read_text())["updates"]["post_update_steps"]["faker-kit"] == {"command": [str(wrapper), "refresh"], "timeout": 1800},
         (done.stdout + done.stderr).strip()[-300:])

    before = hermes_calls()
    again = refresh()
    step("refresh on the commit already installed says so, runs no install, and touches no Hermes profile",
         again.returncode == 0 and again.stdout.strip() == f"kit already current at {first[:12]}" and installed() == first and hermes_calls() == before,
         (again.stdout + again.stderr).strip())

    second = commit(source, "hermes/templates/refresh-marker", "second\n", "second kit commit")
    before = hermes_calls()
    moved = refresh()
    new_calls = calls.read_text().splitlines()[before:]
    step("refresh installs the source's newest main with --no-gateway and prints one line naming both commits",
         moved.returncode == 0 and moved.stdout.strip() == f"kit moved {first[:12]} -> {second[:12]}" and installed() == second and head() == second
         and (state / "kit" / "hermes" / "templates" / "refresh-marker").read_text() == "second\n" and not any('"gateway"' in line for line in new_calls)
         and not list(state.glob(".refresh-*")),
         (moved.stdout + moved.stderr).strip())

    before = hermes_calls()
    repeat = refresh()
    step("a second refresh is a no-op: the same line as already current, no Hermes call, same recorded commit",
         repeat.returncode == 0 and repeat.stdout.strip() == f"kit already current at {second[:12]}" and installed() == second and hermes_calls() == before,
         (repeat.stdout + repeat.stderr).strip())

    hidden = work / "source-away"
    source.rename(hidden)
    unreachable = refresh()
    source_missing = unreachable.returncode == 1 and "kit source" in unreachable.stderr and installed() == second
    hidden.rename(source)
    step("a source that cannot be read refuses with that cause, exits 1, and leaves the installed kit alone",
         source_missing and head() == second, (unreachable.stdout + unreachable.stderr).strip())

    git(source, "rm", "-q", "hermes/bin/faker_kit.py")
    git(source, "commit", "-qm", "drop the command")
    headless = refresh()
    step("a kit commit without the command is refused before the installed kit moves",
         headless.returncode == 1 and "has no hermes/bin/faker_kit.py" in headless.stderr and installed() == second and head() == second
         and (state / "kit" / "hermes" / "bin" / "faker_kit.py").is_file() and not list(state.glob(".refresh-*")),
         (headless.stdout + headless.stderr).strip())

    git(source, "checkout", second, "--", "hermes/bin/faker_kit.py")
    profiles = source / "hermes" / "lib" / "faker_kit_profiles.py"
    fixed = commit(source, "hermes/lib/faker_kit_profiles.py", profiles.read_text().replace("UPDATE_STEP_SECONDS = 1800", "UPDATE_STEP_SECONDS = 1801"), "third kit commit")
    failed = refresh({"E2E_HERMES_CONFIG_FAIL": "1"})
    retried = refresh({"E2E_HERMES_CONFIG_FAIL": "1"})
    step("an install that fails midway refuses with its last line, never claims the kit current, and tries again on the next refresh",
         failed.returncode == 1 and "config set failed" in failed.stderr and installed() == second and head() == fixed
         and retried.returncode == 1 and "config set failed" in retried.stderr and not list(state.glob(".refresh-*")),
         (failed.stdout + failed.stderr).strip())

    recovered = refresh()
    step("the next refresh after the cause is fixed reports the move from the last commit that fully installed, though the checkout already holds the new one",
         recovered.returncode == 0 and recovered.stdout.strip() == f"kit moved {second[:12]} -> {fixed[:12]}" and installed() == fixed and head() == fixed
         and yaml.safe_load((hermes_home / "config.yaml").read_text())["updates"]["post_update_steps"]["faker-kit"]["timeout"] == 1801,
         (recovered.stdout + recovered.stderr).strip())

    with (state / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        newer = commit(source, "hermes/templates/refresh-marker", "fourth\n", "fourth kit commit")
        busy = refresh()
    step("a refresh that finds another one running refuses and changes nothing", busy.returncode == 1 and "already running" in busy.stderr and installed() == fixed,
         (busy.stdout + busy.stderr).strip())

    (state / "kit-source").unlink()
    unknown = refresh()
    step("a kit with no recorded source refuses and says how to record one", unknown.returncode == 1 and "no kit source is recorded" in unknown.stderr and installed() == fixed,
         (unknown.stdout + unknown.stderr).strip())
    (state / "kit-source").write_text(str(source) + "\n")

    (state / "bots.yaml").write_text(yaml.safe_dump({"version": 1, "bots": {
        "ong-xay": {"role": "bootstrap"}, "ba-truong": {"role": "chief"}, "meo-beo": {"role": "project", "project": "shop", "repo": "acme/shop"}}}))
    guarded = {}
    for bot in ("ong-xay", "ba-truong", "meo-beo"):
        profile = hermes_home / "profiles" / bot
        profile.mkdir(parents=True, exist_ok=True)
        guarded[bot] = refresh({"HERMES_HOME": str(profile)})
    step("a bootstrap bot's refresh takes the newest main, a chief's right after finds it current, and a project bot is refused as not its command",
         guarded["ong-xay"].returncode == 0 and guarded["ong-xay"].stdout.strip() == f"kit moved {fixed[:12]} -> {newer[:12]}"
         and guarded["ba-truong"].returncode == 0 and guarded["ba-truong"].stdout.strip() == f"kit already current at {newer[:12]}"
         and guarded["meo-beo"].returncode == 1 and "meo-beo is a project bot; faker-kit refresh is not one of its commands" in guarded["meo-beo"].stderr and installed() == newer,
         json.dumps({bot: (done.returncode, (done.stdout + done.stderr).strip()) for bot, done in guarded.items()}))

    def refused_for_every_bot(*command: str) -> bool:
        bots = [subprocess.run([str(wrapper), *command], env={**env, "HERMES_HOME": str(hermes_home / "profiles" / bot)}, capture_output=True, text=True, timeout=120)
                for bot in guarded]
        return all(done.returncode == 1 and "runs only from the owner's own shell, never from a bot" in done.stderr for done in bots)

    owner_projects = subprocess.run([str(wrapper), "projects"], env=env, capture_output=True, text=True, timeout=120)
    step("install, runner and projects stay owner-only: every bot role is refused, and the owner's own shell still runs projects",
         all(refused_for_every_bot(*command) for command in (("install",), ("runner", "build"), ("projects",))) and owner_projects.returncode == 0 and installed() == newer,
         (owner_projects.stdout + owner_projects.stderr).strip())

    last = commit(source, "hermes/templates/refresh-marker", "fifth\n", "fifth kit commit")
    final = refresh()
    step("with the source reachable and the lock free, the owner's refresh takes the newest main",
         final.returncode == 0 and final.stdout.strip() == f"kit moved {newer[:12]} -> {last[:12]}" and installed() == last, (final.stdout + final.stderr).strip())

    machine = state / "machine.yaml"
    machine.write_text("version: 1\nbootstrap_bot: ong-xay\nguard:\n  trusted_authors:\n    - Codertocat\nprojects:\n  - bot: meo-beo\n    project: shop\n    repo: acme/shop\n")
    machine.chmod(0o600)
    held = machine.read_bytes()
    newest = commit(source, "hermes/templates/refresh-marker", "sixth\n", "sixth kit commit")
    away = work / "cwd-away"
    away.mkdir()

    def host_update() -> tuple[subprocess.CompletedProcess[str], dict]:
        done = subprocess.run([str(wrapper), "host", "update", "--json"], env=env, cwd=away, capture_output=True, text=True, timeout=600)
        try:
            return done, json.loads(done.stdout)
        except ValueError:
            return done, {}

    done, moved = host_update()
    step("host update --json from another directory, with no clone and no ref, takes the source's newest main and prints one envelope with from and to",
         done.returncode == 0 and done.stdout.count("\n") == 1 and moved.get("ok") is True and moved["data"]["from_commit"] == last and moved["data"]["to_commit"] == newest
         and moved["data"]["changed"] is True and installed() == newest and [s["name"] for s in moved["data"]["steps"]] == ["kit", "reload_routes"]
         and machine.read_bytes() == held and stat.S_IMODE(machine.stat().st_mode) == 0o600, (done.stdout + done.stderr).strip()[:300])

    done, current = host_update()
    step("a second host update finds the kit current, reports changed false, and leaves machine.yaml byte for byte as it was",
         done.returncode == 0 and current["data"]["changed"] is False and current["data"]["to_commit"] == newest and machine.read_bytes() == held, (done.stdout + done.stderr).strip()[:300])

    newer_still = commit(source, "hermes/templates/refresh-marker", "seventh\n", "seventh kit commit")
    with (state / "refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        done, busy = host_update()
    step("a host update that finds a refresh running returns update_in_progress and leaves the installed kit alone",
         done.returncode == 1 and busy.get("code") == "update_in_progress" and busy["ok"] is False and installed() == newest and newer_still != newest, (done.stdout + done.stderr).strip()[:300])

    (state / "kit-source").unlink()
    done, nosource = host_update()
    (state / "kit-source").write_text(str(source) + "\n")
    step("a host update with no recorded kit source returns kit_source_missing with contact_support and no path to run",
         done.returncode == 1 and nosource.get("code") == "kit_source_missing" and nosource["next"]["action"] == "contact_support", (done.stdout + done.stderr).strip()[:300])

    refused = subprocess.run([str(wrapper), "host", "update", "--json"], env={**env, "HERMES_HOME": str(hermes_home / "profiles" / "meo-beo")}, cwd=away, capture_output=True, text=True, timeout=120)
    step("a project bot is refused host update with a typed envelope and the kit stays where it was",
         refused.returncode == 1 and json.loads(refused.stdout)["code"] == "request_refused" and installed() == newest, (refused.stdout + refused.stderr).strip()[:300])
