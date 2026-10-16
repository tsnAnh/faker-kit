"""The runner container's one custom seccomp profile: Docker's default plus two named additions, and nothing else."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from team_kit_runner import SECCOMP_PROFILE

DEFAULT = Path(__file__).resolve().parent / "fixtures" / "docker-default-seccomp.json"
ADDITIONS = [
    {"names": ["unshare"], "action": "SCMP_ACT_ALLOW"},
    {"names": ["mount"], "action": "SCMP_ACT_ALLOW"},
]


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_the_profile_is_the_default_plus_exactly_the_listed_additions():
    base, profile = load(DEFAULT), load(SECCOMP_PROFILE)
    extra_keys = {k for k in profile if k.startswith("x-team-kit-")}
    assert {k: v for k, v in profile.items() if k not in extra_keys and k != "syscalls"} == {k: v for k, v in base.items() if k != "syscalls"}
    assert profile["syscalls"][: len(base["syscalls"])] == base["syscalls"], "every default rule is unchanged and first"
    assert profile["syscalls"][len(base["syscalls"]):] == ADDITIONS, "anything else allowed fails here"


def test_each_addition_is_named_in_the_header_with_its_failure():
    profile = load(SECCOMP_PROFILE)
    assert "Docker's default seccomp profile" in profile["x-team-kit-derived-from"] and "29.8.2" in profile["x-team-kit-derived-from"]
    listed = [a["syscalls"] for a in profile["x-team-kit-additions"]]
    assert listed == [rule["names"] for rule in ADDITIONS]
    assert all("not permitted" in a["why"] for a in profile["x-team-kit-additions"])


def test_no_default_denial_is_loosened():
    profile = load(SECCOMP_PROFILE)
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    allowed_unconditionally = {n for rule in profile["syscalls"] if rule["action"] == "SCMP_ACT_ALLOW" and not rule.get("includes") and not rule.get("args") for n in rule["names"]}
    for name in ("reboot", "kexec_load", "init_module", "finit_module", "bpf", "setns", "pivot_root", "clone", "clone3", "umount2"):
        assert name not in allowed_unconditionally, name


ARCH_SYSCALLS = {"x86_64": {"reboot": 169, "kexec_load": 246, "init_module": 175, "bpf": 321}, "aarch64": {"reboot": 142, "kexec_load": 104, "init_module": 105, "bpf": 280}}
PROBE = """
import ctypes, os, platform, sys
libc = ctypes.CDLL(None, use_errno=True)
nr = {nrs!r}
def errno_of(rc):
    return 0 if rc == 0 else ctypes.get_errno()
out = {{}}
out["reboot"] = errno_of(libc.syscall(nr["reboot"], 0xfee1dead, 672274793, 0x1234567, 0))
out["kexec_load"] = errno_of(libc.syscall(nr["kexec_load"], 0, 0, 0, 0))
out["init_module"] = errno_of(libc.syscall(nr["init_module"], 0, 0, 0))
out["bpf"] = errno_of(libc.syscall(nr["bpf"], 0, 0, 0))
out["mount"] = errno_of(libc.mount(b"none", b"/mnt", b"tmpfs", 0, None))
out["ptrace_non_child"] = errno_of(libc.ptrace(16, 1, 0, 0))
import json; print(json.dumps(out))
"""


@pytest.mark.skipif(os.environ.get("TEAM_KIT_DOCKER_TESTS") != "1" or shutil.which("docker") is None, reason="needs a Docker daemon: set TEAM_KIT_DOCKER_TESTS=1 and TEAM_KIT_RUNNER_IMAGE")
def test_the_retained_denials_hold_inside_a_container_under_the_profile():
    image = os.environ["TEAM_KIT_RUNNER_IMAGE"]
    arch = subprocess.run(["docker", "run", "--rm", "--entrypoint", "uname", image, "-m"], capture_output=True, text=True, check=True).stdout.strip()
    probe = PROBE.format(nrs=ARCH_SYSCALLS[arch])
    done = subprocess.run(["docker", "run", "--rm", "--entrypoint", "python3", "--security-opt", f"seccomp={SECCOMP_PROFILE}", image, "-c", probe],
                          capture_output=True, text=True, check=True)
    result = json.loads(done.stdout)
    assert result == {"reboot": 1, "kexec_load": 1, "init_module": 1, "bpf": 1, "mount": 1, "ptrace_non_child": 1}, result
