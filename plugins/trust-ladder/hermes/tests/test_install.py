import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import HERMES, KIT

INSTALL = HERMES / "install.sh"

FAKE_HERMES = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
root = Path(os.environ["HOME"]) / ".hermes"
args = sys.argv[1:]
with open(root / "calls.log", "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["profile", "create"]:
    (root / "profiles" / args[2]).mkdir(parents=True)
if args[2:4] == ["cron", "list"]:
    print((root / "calls.log").read_text())
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def kit_source(tmp_path_factory):
    """A git repository holding this working tree's trust-ladder plugin, so the test installs what is on disk."""
    src = tmp_path_factory.mktemp("kit")
    shutil.copytree(KIT / "plugins" / "trust-ladder", src / "plugins" / "trust-ladder", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    git(src, "init", "-q")
    git(src, "config", "uploadpack.allowAnySHA1InWant", "true")
    git(src, "add", ".")
    git(src, "commit", "-qm", "kit")
    return src, git(src, "rev-parse", "HEAD")


@pytest.fixture
def machine(tmp_path):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    hermes = bin_dir / "hermes"
    hermes.write_text(FAKE_HERMES)
    hermes.chmod(hermes.stat().st_mode | stat.S_IEXEC)
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    return home / ".hermes", env


def install(kit_source, machine, *extra):
    src, sha = kit_source
    root, env = machine
    return subprocess.run(["bash", str(INSTALL), "--ref", sha, "--source", str(src), *extra], env=env, capture_output=True, text=True)


def calls(root):
    return [json.loads(line) for line in (root / "calls.log").read_text().splitlines()]


def test_installs_the_hermes_side_pinned_to_the_commit(kit_source, machine):
    root, _ = machine
    done = install(kit_source, machine, "--dispatcher-model", "strong-model")
    assert done.returncode == 0, done.stderr
    assert git(root / "team-kit" / "kit", "rev-parse", "HEAD") == kit_source[1]

    assert sorted(p.name for p in (root / "skills").iterdir()) == ["dispatch", "setup-dispatch"]
    assert sorted(p.name for p in (root / "profiles" / "dispatcher" / "skills").iterdir()) == ["dispatch", "setup-dispatch"]
    assert not (root / "skills" / "faker-mode").exists()

    for profile in ("dispatcher", "launcher"):
        home = root / "profiles" / profile
        config = yaml.safe_load((home / "config.yaml").read_text())
        assert config["plugins"]["enabled"] == ["team_kit_guard"]
        assert config["approvals"] == {"mode": "manual", "cron_mode": "deny"}
        assert (home / "plugins" / "team_kit_guard" / "plugin.yaml").exists()
        assert (home / "SOUL.md").read_text().startswith(f"You are the team-kit {profile}.")

    dispatcher = yaml.safe_load((root / "profiles" / "dispatcher" / "config.yaml").read_text())
    route = dispatcher["platforms"]["webhook"]["extra"]["routes"]["team-kit-github"]
    secret = (root / "team-kit" / "webhook-secret").read_text().strip()
    assert len(secret) == 64 and route["secret"] == secret
    assert stat.S_IMODE((root / "team-kit" / "webhook-secret").stat().st_mode) == 0o600
    assert route["script"] == "team-kit/github-triage.py" and route["cron_job"] == "dispatcher-drain"
    assert "toolsets" not in route
    assert (root / "profiles" / "dispatcher" / "scripts" / "team-kit" / route["script"].split("/")[1]).exists()
    assert (root / "team-kit" / "guard.yaml").exists()

    log = calls(root)
    assert ["-p", "dispatcher", "config", "set", "model.default", "strong-model"] in log
    crons = {c[4]: c[5:] for c in log if c[:4] == ["-p", "dispatcher", "cron", "create"]}
    assert sorted(crons) == ["0 9,13,17 * * *", "every 2h"]
    status = crons["0 9,13,17 * * *"]
    assert status[0].startswith("Write the dispatch status digest")
    assert status[1:] == ["--name", "dispatch-status", "--deliver", "bot-chat:dispatcher", "--skill", "dispatch"]
    assert crons["every 2h"][1:3] == ["--name", "dispatcher-drain"]
    assert ["-p", "dispatcher", "gateway", "install"] in log


def test_a_second_run_keeps_guard_yaml_the_secret_and_the_cron_jobs(kit_source, machine):
    root, _ = machine
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    (root / "team-kit" / "guard.yaml").write_text("version: 1\nrepos: [acme/shop]\n")
    secret = (root / "team-kit" / "webhook-secret").read_text()
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    assert (root / "team-kit" / "guard.yaml").read_text() == "version: 1\nrepos: [acme/shop]\n"
    assert (root / "team-kit" / "webhook-secret").read_text() == secret
    assert sum(c[2:4] == ["cron", "create"] for c in calls(root)) == 2
    assert not any("gateway" in c for c in calls(root))


def test_the_team_kit_gh_wrapper_runs_the_installed_script(kit_source, machine):
    root, env = machine
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    done = subprocess.run([str(root / "bin" / "team-kit-gh"), "kit-check", "--repo", "acme/elsewhere"], env=env, capture_output=True, text=True)
    assert done.returncode == 1
    assert "not in guard.yaml repos" in done.stderr


def test_refuses_a_short_ref(machine):
    _, env = machine
    done = subprocess.run(["bash", str(INSTALL), "--ref", "abc1234"], env=env, capture_output=True, text=True)
    assert done.returncode == 1
    assert "full 40-character commit" in done.stderr
