#!/usr/bin/env python3
"""team-kit-runner: run repositories' GitHub Actions jobs on this machine, one fresh container per job.

  up --config PATH          build the runner image, start one background service per slot in the machine
                            config's ci_runners, and stop the services of slots it no longer lists
  slot --repo R --slot N    what each service runs: forever, register a just-in-time runner for one job,
                            run it in a new container, then remove it
  build                     build the runner image

A job's container is Ubuntu, like a GitHub-hosted runner, and sees nothing of this machine: no Hermes files,
no gh login, no bot secrets. GitHub gives each runner a one-job configuration; it reaches the container on
stdin, never this machine's argv, environment, or files. Workflows pick these runners with
`runs-on: [self-hosted, team-kit]`. Every decision here is made in code: what to register, when, and how
long to wait after a failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
# Last, so an installed PyYAML wins; the vendored copy keeps the command working on a Python without one.
if (HERE.parent / "vendor").is_dir() and str(HERE.parent / "vendor") not in sys.path:
    sys.path.append(str(HERE.parent / "vendor"))

from team_kit_machine import REPO, MachineError, load_machine

IMAGE_DIR = HERE.parent / "ci-runner"
SECCOMP_PROFILE = IMAGE_DIR / "seccomp.json"  # the one relaxation of the job container; see docs/how-to/ci-runner-image-builds.md
IMAGE = "team-kit-ci-runner"
LABEL = "team-kit"
SERVICE_PREFIX = "team-kit-ci-"
JOB_TIMEOUT_SECONDS = 7 * 3600  # GitHub stops a job at 6 hours by default; past this the container is stuck
BACKOFF_SECONDS = (30, 60, 120, 300)
SLUG = re.compile(r"[^a-z0-9]+")

Runner = Callable[..., subprocess.CompletedProcess]


class RunnerError(RuntimeError):
    pass


def slug(text: str) -> str:
    return SLUG.sub("-", text.lower()).strip("-")


def service_name(repo: str, slot: int) -> str:
    return f"{SERVICE_PREFIX}{slug(repo)}-{slot}"


def image_tag() -> str:
    """The image's tag names its sources, so a kit update that changes them builds a new image."""
    digest = hashlib.sha256()
    for path in sorted(IMAGE_DIR.iterdir()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return f"{IMAGE}:{digest.hexdigest()[:12]}"


def first_line(text: str | None) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "no detail")[:200]


def arch_label() -> str:
    machine = platform.machine().lower()
    return {"aarch64": "ARM64", "arm64": "ARM64", "x86_64": "X64", "amd64": "X64"}.get(machine, machine.upper())


def build(run: Runner = subprocess.run) -> str:
    tag = image_tag()
    if run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0:
        done = run(["docker", "build", "-q", "-t", tag, str(IMAGE_DIR)], capture_output=True, text=True)
        if done.returncode != 0:
            raise RunnerError(f"docker build failed: {first_line(done.stderr)}")
    return tag


class Slot:
    """One runner slot of one repository: one job at a time, each in a container of its own."""

    def __init__(self, repo: str, slot: int, run: Runner = subprocess.run, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.time, host: str | None = None) -> None:
        self.repo, self.slot, self.run, self.sleep, self.now = repo, slot, run, sleep, now
        self.host = slug(host or socket.gethostname().split(".")[0])[:24] or "host"
        self.container = service_name(repo, slot)

    def register(self) -> tuple[int, str]:
        """A just-in-time runner for one job: its id, and the configuration only that container gets."""
        body = {"name": f"{self.host}-{self.slot}-{int(self.now())}", "runner_group_id": 1,
                "labels": ["self-hosted", "linux", arch_label(), LABEL], "work_folder": "_work"}
        done = self.run(["gh", "api", "-X", "POST", f"repos/{self.repo}/actions/runners/generate-jitconfig", "--input", "-"],
                        input=json.dumps(body), capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            raise RunnerError(f"GitHub gave no runner for {self.repo}: {first_line(done.stderr)}")
        try:
            data = json.loads(done.stdout)
            return int(data["runner"]["id"]), str(data["encoded_jit_config"])
        except (ValueError, KeyError, TypeError):
            raise RunnerError(f"GitHub's runner answer for {self.repo} was not readable") from None

    def once(self) -> int:
        """Run one job. Returns the container's exit code."""
        tag = build(self.run)
        self.run(["docker", "rm", "-f", self.container], capture_output=True)  # left behind by a crash or a stop
        runner_id, config = self.register()
        try:
            done = self.run(["docker", "run", "--rm", "-i", "--security-opt", f"seccomp={SECCOMP_PROFILE}", "--name", self.container, tag],
                            input=config + "\n", text=True, timeout=JOB_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.run(["docker", "rm", "-f", self.container], capture_output=True)
            raise RunnerError(f"a job on {self.repo} ran past {JOB_TIMEOUT_SECONDS // 3600} hours; its container was removed") from None
        finally:
            # GitHub removes a just-in-time runner after its job; this removes one that never got a job.
            self.run(["gh", "api", "-X", "DELETE", f"repos/{self.repo}/actions/runners/{runner_id}"], capture_output=True, timeout=60)
        return done.returncode

    def forever(self, rounds: int | None = None) -> None:
        failures = 0
        while rounds is None or rounds > 0:
            try:
                code = self.once()
                failures = 0 if code == 0 else failures + 1
                if code != 0:
                    print(f"team-kit-runner: {self.repo} slot {self.slot}: the runner container exited {code}", file=sys.stderr)
            except (RunnerError, OSError, subprocess.SubprocessError) as err:
                failures += 1
                print(f"team-kit-runner: {self.repo} slot {self.slot}: {err}", file=sys.stderr)
            if failures:
                self.sleep(BACKOFF_SECONDS[min(failures, len(BACKOFF_SECONDS)) - 1])
            if rounds is not None:
                rounds -= 1


class Services:
    """Background services that keep each slot running: launchd agents on macOS, systemd user units on Linux."""

    def __init__(self, home: Path, system: str, run: Runner = subprocess.run) -> None:
        self.home, self.system, self.run = home, system, run
        self.logs = home / ".hermes" / "team-kit" / "logs"

    def path(self, name: str) -> Path:
        if self.system == "Darwin":
            return self.home / "Library" / "LaunchAgents" / f"{name}.plist"
        return self.home / ".config" / "systemd" / "user" / f"{name}.service"

    def existing(self) -> set[str]:
        folder = self.path("x").parent
        suffix = ".plist" if self.system == "Darwin" else ".service"
        return {p.name.removesuffix(suffix) for p in folder.glob(f"{SERVICE_PREFIX}*{suffix}")} if folder.is_dir() else set()

    def start(self, name: str, argv: list[str], path_env: str) -> None:
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.logs.mkdir(parents=True, exist_ok=True)
        log = str(self.logs / f"{name}.log")
        if self.system == "Darwin":
            target.write_bytes(plistlib.dumps({"Label": name, "ProgramArguments": argv, "RunAtLoad": True, "KeepAlive": True,
                                               "EnvironmentVariables": {"PATH": path_env}, "StandardOutPath": log, "StandardErrorPath": log}))
            domain = f"gui/{os.getuid()}"
            self.run(["launchctl", "bootout", f"{domain}/{name}"], capture_output=True)
            done = self.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True, text=True)
        else:
            quoted = " ".join('"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"' for a in argv)
            target.write_text(f"[Unit]\nDescription={name}\nAfter=network-online.target\nWants=network-online.target\n\n"
                              f"[Service]\nType=simple\nEnvironment=\"PATH={path_env}\"\nExecStart={quoted}\nRestart=always\nRestartSec=10\n"
                              f"StandardOutput=append:{log}\nStandardError=append:{log}\n\n[Install]\nWantedBy=default.target\n")
            self.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            done = self.run(["systemctl", "--user", "enable", "--now", f"{name}.service"], capture_output=True, text=True)
            self.run(["systemctl", "--user", "restart", f"{name}.service"], capture_output=True)
        if done.returncode != 0:
            raise RunnerError(f"could not start {name}: {first_line(done.stderr)}")

    def stop(self, name: str) -> None:
        if self.system == "Darwin":
            self.run(["launchctl", "bootout", f"gui/{os.getuid()}/{name}"], capture_output=True)
        else:
            self.run(["systemctl", "--user", "disable", "--now", f"{name}.service"], capture_output=True)
        self.path(name).unlink(missing_ok=True)
        self.run(["docker", "rm", "-f", name], capture_output=True)


def up(config: Path, services: Services, run: Runner = subprocess.run, path_env: str | None = None) -> dict:
    machine = load_machine(config)
    wanted = {service_name(r.repo, n): (r.repo, n) for r in machine.ci_runners for n in range(1, r.slots + 1)}
    stopped = sorted(services.existing() - set(wanted))
    for name in stopped:
        services.stop(name)
    if not wanted:
        return {"image": None, "started": [], "stopped": stopped}
    tag = build(run)
    for name, (repo, n) in sorted(wanted.items()):
        services.start(name, [sys.executable, str(Path(__file__).resolve()), "slot", "--repo", repo, "--slot", str(n)],
                       path_env or os.environ.get("PATH", "/usr/bin:/bin"))
    return {"image": tag, "started": sorted(wanted), "stopped": stopped,
            "runs_on": ["self-hosted", LABEL]}


def check_slot(repo: str, slot: int) -> None:
    if not REPO.match(repo) or not 1 <= slot <= 8:
        raise RunnerError("--repo must be OWNER/REPO and --slot a number from 1 to 8")


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="team-kit-runner")
    sub = top.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("up")
    cmd.add_argument("--config", required=True, type=Path)
    cmd = sub.add_parser("slot")
    cmd.add_argument("--repo", required=True)
    cmd.add_argument("--slot", required=True, type=int)
    sub.add_parser("build")
    return top


def main(argv: list[str] | None = None, run: Runner = subprocess.run) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "slot":
            check_slot(args.repo, args.slot)
            Slot(args.repo, args.slot, run).forever()
            return 0
        out = {"image": build(run)} if args.command == "build" else up(args.config, Services(Path.home(), platform.system(), run), run)
    except (RunnerError, MachineError) as err:
        print(f"team-kit-runner: refused: {err}", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
