from __future__ import annotations

import fcntl
import shutil
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
        return subprocess.run(["bash", str(source / "hermes" / "install.sh"), "--ref", ref, "--source", str(source), "--no-vendor", "--no-gateway"],
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
    step("refresh installs the source's newest main with --no-vendor --no-gateway and prints one line naming both commits",
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
    (state / "kit-repo").unlink(missing_ok=True)
    unknown = refresh()
    step("a kit with no recorded source refuses and says how to record one", unknown.returncode == 1 and "no kit source is recorded" in unknown.stderr and installed() == fixed,
         (unknown.stdout + unknown.stderr).strip())
    (state / "kit-source").write_text(str(source) + "\n")

    profile = hermes_home / "profiles" / "some-bot"
    profile.mkdir(parents=True)
    from_bot = refresh({"HERMES_HOME": str(profile)})
    step("a bot cannot run refresh", from_bot.returncode == 1 and "owner's own shell" in from_bot.stderr and installed() == fixed,
         (from_bot.stdout + from_bot.stderr).strip())

    final = refresh()
    step("with the source reachable and the lock free, refresh takes the newest main",
         final.returncode == 0 and final.stdout.strip() == f"kit moved {fixed[:12]} -> {newer[:12]}" and installed() == newer, (final.stdout + final.stderr).strip())
