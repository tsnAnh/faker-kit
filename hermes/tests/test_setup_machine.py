"""hermes/setup-machine.sh end to end on a fake Linux machine: every tool it calls is a double on PATH,
and the kit it installs is this working tree."""
import json
import stat
import subprocess
import sys

import pytest
import yaml
from conftest import executable, gh_calls, hermes_calls

LOG = "tools.log"


def tool(bin_dir, name, body):
    """A fake system tool that logs its arguments, then runs body (Python)."""
    executable(bin_dir / name, f"#!{sys.executable}\nimport json, os, sys\nargs = sys.argv[1:]\n"
               f"open(os.path.join(os.environ['HOME'], {LOG!r}), 'a').write(json.dumps([{name!r}, *args]) + '\\n')\n{body}\n")


@pytest.fixture
def linux(kit_source, machine, tmp_path):
    src, sha = kit_source
    root, env = machine
    bin_dir = tmp_path / "bin"
    (bin_dir / "python3").symlink_to(sys.executable)
    tool(bin_dir, "uname", "print('Linux')")
    tool(bin_dir, "systemctl", "sys.exit(0)")
    tool(bin_dir, "loginctl", "print('Linger=yes')")
    tool(bin_dir, "sudo", "sys.exit('sudo must not be needed on a machine that already has everything')")
    tool(bin_dir, "curl", "sys.exit(7)  # nothing listens: a machine without webhooks never needs the listener")
    tool(bin_dir, "claude", "print('2.1.300 (Claude Code)') if args == ['--version'] else sys.exit(0)")
    tool(bin_dir, "sshd", "sys.exit(0)")
    state = root.parent / ".fake-gh"
    state.mkdir(exist_ok=True)
    (state / "files.json").write_text(json.dumps({"acme/shop": {".agents/team-kit/KIT.json": json.dumps({"version": 1, "kit_ref": sha}),
                                                                ".agents/dispatch.json": json.dumps({"backend": "claude", "kit_ref": sha})}}))
    config = tmp_path / "machine.yaml"
    config.write_text(yaml.safe_dump({
        "version": 1, "bootstrap_bot": "ong-xay", "claude_subscription": False,
        "guard": {"trusted_authors": ["Codertocat"], "ask_owner": False, "max_in_flight_total": 4},
        "projects": [{"bot": "meo-beo", "project": "shop", "repo": "acme/shop", "tick_every": "10m"}],
        "chief": {"bot": "chanh-van"}}))
    return root, env, src, sha, config


def setup(linux, token="ghp_owner_pasted_token\n"):
    _root, env, src, sha, config = linux
    return subprocess.run(["bash", str(src / "hermes" / "setup-machine.sh"), "--config", str(config), "--ref", sha, "--yes"],
                          env=env, input=token, capture_output=True, text=True)


def tools(root):
    path = root.parent / LOG
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_one_run_builds_the_machine_and_a_second_run_changes_nothing(linux):
    root = linux[0]
    done = setup(linux)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Done" in done.stdout

    env = root / "profiles" / "meo-beo" / ".env"
    assert "GH_TOKEN=ghp_owner_pasted_token" in env.read_text().splitlines()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    pasted = "ghp_owner_pasted_token"
    assert pasted not in done.stdout + done.stderr, "a pasted token is never echoed"
    assert all(pasted not in json.dumps(c) for c in [*tools(root), *gh_calls(root), *hermes_calls(root)]), "nor passed as an argument"

    bots = yaml.safe_load((root / "team-kit" / "bots.yaml").read_text())["bots"]
    assert {name: bot["role"] for name, bot in bots.items()} == {"ong-xay": "bootstrap", "meo-beo": "project", "chanh-van": "chief"}
    jobs = json.loads((root / "profiles" / "meo-beo" / "jobs.json").read_text())
    assert jobs and all(job["enabled"] and job["no_agent"] for job in jobs), "the token was there, so the jobs run"
    guard = yaml.safe_load((root / "team-kit" / "guard.yaml").read_text())
    assert (guard["ask_owner"], guard["max_in_flight_total"]) == (False, 4)
    gateway = [c for c in hermes_calls(root) if c[:3] == ["-p", "default", "gateway"]]
    assert [c[3] for c in gateway] == ["install", "restart"], "the gateway starts once, after the bots exist"
    assert "still to do: cd " in done.stdout, "without a terminal, the folder-trust step is left for the owner"

    again = setup(linux, token="")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "meo-beo: GH_TOKEN is set" in again.stdout
    assert "GH_TOKEN=ghp_owner_pasted_token" in env.read_text().splitlines()
    assert json.loads((root / "profiles" / "meo-beo" / "jobs.json").read_text()) == jobs


def test_an_unfilled_config_stops_before_anything_is_installed(linux):
    root, _env, _src, _sha, config = linux
    data = yaml.safe_load(config.read_text())
    data["webhooks"] = {"url": "https://hermes.CHANGE-ME.example", "tunnel": "hermes"}
    config.write_text(yaml.safe_dump(data))
    done = setup(linux)
    assert done.returncode == 1
    assert "fill it in first" in done.stderr
    assert not (root / "team-kit").exists() and not hermes_calls(root)


def test_a_kit_commit_without_machine_setup_is_refused_before_anything_is_installed(linux):
    root, env, src, _sha, config = linux
    done = subprocess.run(["bash", str(src / "hermes" / "setup-machine.sh"), "--config", str(config), "--ref", "0" * 40, "--yes"],
                          env=env, input="", capture_output=True, text=True)
    assert done.returncode == 1 and "has no machine setup" in done.stderr
    assert not (root / "team-kit").exists()
