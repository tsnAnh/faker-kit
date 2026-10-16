"""team-kit-runner: one just-in-time runner per job, in a container that gets its configuration on stdin."""
import json
import plistlib
import subprocess

import pytest
import yaml
from team_kit_runner import BACKOFF_SECONDS, SECCOMP_PROFILE, Services, Slot, image_tag, main, service_name, up

CONFIG = "eyJzZWNyZXQiOiJvbmUtam9iIn0="  # what GitHub returns as encoded_jit_config: the runner's one-job credentials


class Fake:
    """gh and docker as the slot calls them. Records every call with its stdin."""

    def __init__(self, jit_code=0, run_code=0, image=True, timeout=False):
        self.calls, self.jit_code, self.run_code, self.image, self.timeout = [], jit_code, run_code, image, timeout

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw.get("input")))
        out, code, err = "", 0, ""
        if argv[:2] == ["docker", "image"]:
            code = 0 if self.image else 1
        elif argv[:2] == ["docker", "run"]:
            if self.timeout:
                raise subprocess.TimeoutExpired(argv, 1)
            code = self.run_code
        elif "generate-jitconfig" in " ".join(argv):
            code = self.jit_code
            out = json.dumps({"runner": {"id": 42}, "encoded_jit_config": CONFIG}) if code == 0 else ""
            err = "" if code == 0 else "gh: Resource not accessible by integration (HTTP 403)"
        return subprocess.CompletedProcess(argv, code, out, err)

    def argvs(self):
        return [a for a, _ in self.calls]


def slot(fake, **kw):
    return Slot("Acme/Shop", 2, run=fake, sleep=kw.pop("sleep", lambda s: None), now=lambda: 1000, host="Ben's Mac.local", **kw)


def test_one_job_gets_a_fresh_container_and_its_config_only_on_stdin():
    fake = Fake()
    assert slot(fake).once() == 0
    register = next((a, i) for a, i in fake.calls if "generate-jitconfig" in " ".join(a))
    assert register[0][:4] == ["gh", "api", "-X", "POST"] and register[0][-2:] == ["--input", "-"]
    body = json.loads(register[1])
    assert body["labels"][0] == "self-hosted" and "team-kit" in body["labels"] and body["runner_group_id"] == 1
    assert body["name"] == "ben-s-mac-local-2-1000"
    [(run_argv, stdin)] = [(a, i) for a, i in fake.calls if a[:2] == ["docker", "run"]]
    assert run_argv == ["docker", "run", "--rm", "-i", "--security-opt", f"seccomp={SECCOMP_PROFILE}", "--name", "team-kit-ci-acme-shop-2", image_tag()]
    assert stdin == CONFIG + "\n"
    assert all(CONFIG not in " ".join(a) for a in fake.argvs()), "the one-job credentials never reach an argv"
    assert fake.argvs()[-1] == ["gh", "api", "-X", "DELETE", "repos/Acme/Shop/actions/runners/42"], "a runner that got no job is removed"


def test_the_job_container_gets_one_security_option_and_no_other_privilege():
    fake = Fake()
    slot(fake).once()
    [run_argv] = [a for a in fake.argvs() if a[:2] == ["docker", "run"]]
    assert [a for a in run_argv if a.startswith("--") or a == "-i"] == ["--rm", "-i", "--security-opt", "--name"]
    assert run_argv.count("--security-opt") == 1
    assert run_argv[run_argv.index("--security-opt") + 1] == f"seccomp={SECCOMP_PROFILE}"
    assert SECCOMP_PROFILE.name == "seccomp.json" and SECCOMP_PROFILE.parent.name == "ci-runner" and SECCOMP_PROFILE.is_file()
    line = " ".join(run_argv)
    for forbidden in ("--privileged", "--cap-add", "docker.sock", "unconfined", "apparmor", "--volume", " -v ", "--mount", "--device", "--pid", "--network", "--userns"):
        assert forbidden not in line, forbidden
    assert "--rm" in run_argv, "the container is removed after the job"


def test_a_leftover_container_is_removed_before_the_next_job():
    fake = Fake()
    slot(fake).once()
    argvs = fake.argvs()
    assert argvs.index(["docker", "rm", "-f", "team-kit-ci-acme-shop-2"]) < next(i for i, a in enumerate(argvs) if a[:2] == ["docker", "run"])


def test_a_missing_image_is_built_first():
    fake = Fake(image=False)
    slot(fake).once()
    assert ["docker", "build", "-q", "-t", image_tag()] == fake.argvs()[1][:5]


def test_a_stuck_job_is_removed_and_the_runner_deregistered():
    fake = Fake(timeout=True)
    with pytest.raises(Exception, match="ran past 7 hours"):
        slot(fake).once()
    assert fake.argvs()[-2:] == [["docker", "rm", "-f", "team-kit-ci-acme-shop-2"], ["gh", "api", "-X", "DELETE", "repos/Acme/Shop/actions/runners/42"]]


def test_failures_back_off_and_never_start_a_container(capsys):
    waits = []
    fake = Fake(jit_code=1)
    slot(fake, sleep=waits.append).forever(rounds=6)
    assert waits == [*BACKOFF_SECONDS, BACKOFF_SECONDS[-1], BACKOFF_SECONDS[-1]]
    assert not [a for a in fake.argvs() if a[:2] == ["docker", "run"]]
    assert "GitHub gave no runner for Acme/Shop: gh: Resource not accessible" in capsys.readouterr().err


def test_a_finished_job_starts_the_next_at_once():
    waits = []
    slot(Fake(), sleep=waits.append).forever(rounds=3)
    assert waits == []


@pytest.mark.parametrize("argv", [["slot", "--repo", "acme", "--slot", "1"], ["slot", "--repo", "acme/shop", "--slot", "9"]])
def test_slot_refuses_bad_arguments(argv, capsys):
    assert main(argv, run=Fake()) == 1
    assert "--repo must be OWNER/REPO" in capsys.readouterr().err


def machine(tmp_path, runners):
    path = tmp_path / "machine.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "bootstrap_bot": "ong-xay", "guard": {"trusted_authors": ["Codertocat"]},
                                    "projects": [{"bot": "meo-beo", "project": "shop", "repo": "acme/shop"}], "ci_runners": runners}))
    return path


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_up_starts_one_service_per_slot_and_stops_slots_no_longer_listed(tmp_path, system):
    fake = Fake()
    services = Services(tmp_path, system, run=fake)
    stale = services.path(service_name("acme/old", 1))
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    out = up(machine(tmp_path, [{"repo": "acme/shop", "slots": 2}, {"repo": "TitoniumLab/Lectrostein"}]), services, run=fake, path_env="/usr/bin:/bin")
    assert out["started"] == ["team-kit-ci-acme-shop-1", "team-kit-ci-acme-shop-2", "team-kit-ci-titoniumlab-lectrostein-1"]
    assert out["stopped"] == ["team-kit-ci-acme-old-1"] and not stale.exists()
    assert ["docker", "rm", "-f", "team-kit-ci-acme-old-1"] in fake.argvs()
    unit = services.path("team-kit-ci-acme-shop-2")
    if system == "Darwin":
        plist = plistlib.loads(unit.read_bytes())
        assert plist["ProgramArguments"][-4:] == ["--repo", "acme/shop", "--slot", "2"] and plist["KeepAlive"] is True
        assert plist["EnvironmentVariables"] == {"PATH": "/usr/bin:/bin"}
    else:
        text = unit.read_text()
        assert '"--repo" "acme/shop" "--slot" "2"' in text and "Restart=always" in text


def test_up_without_runners_builds_nothing(tmp_path):
    fake = Fake()
    out = up(machine(tmp_path, []), Services(tmp_path, "Linux", run=fake), run=fake)
    assert out == {"image": None, "started": [], "stopped": []}
    assert not fake.calls
