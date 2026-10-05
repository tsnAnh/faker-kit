import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import HERMES, git, hermes_calls, install

FAKE_HERMES = f"#!{sys.executable}\n" + (Path(__file__).with_name("fake_hermes.py")).read_text()


def read_config(root, profile="default"):
    home = root if profile == "default" else root / "profiles" / profile
    return yaml.safe_load((home / "config.yaml").read_text())


def seed_config(root, profile, config):
    home = root if profile == "default" else root / "profiles" / profile
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump(config))


def test_installs_the_module_and_the_bootstrap_bot_pinned_to_the_commit(kit_source, machine):
    root, _ = machine
    done = install(kit_source, machine, "--bootstrap-bot", "ong-xay", "--model", "strong-model")
    assert done.returncode == 0, done.stderr
    kit = root / "team-kit" / "kit"
    assert git(kit, "rev-parse", "HEAD") == kit_source[1]

    assert yaml.safe_load((root / "team-kit" / "bots.yaml").read_text())["bots"] == {"ong-xay": {"role": "bootstrap"}}
    assert stat.S_IMODE((root / "team-kit" / "bots.yaml").stat().st_mode) == 0o600
    home = root / "profiles" / "ong-xay"
    config = read_config(root, "ong-xay")
    assert config["toolsets"] == ["terminal", "clarify", "skills"]
    assert config["plugins"]["enabled"] == ["team_kit_guard"]
    assert config["approvals"] == {"mode": "manual", "cron_mode": "deny"}
    assert config["model"] == {"default": "strong-model"}
    assert (home / "plugins" / "team_kit_guard" / "plugin.yaml").exists()
    assert (home / "SOUL.md").read_text().startswith("You are this machine's team-kit bootstrap bot.")
    assert sorted(p.name for p in (home / "skills").iterdir()) == ["bootstrap"]
    assert (home / "skills" / "bootstrap").resolve() == (kit / "hermes" / "skills" / "bootstrap").resolve()
    assert not (root / "skills").exists(), "the host profile gets no team-kit skills"

    assert not (root / "team-kit" / "webhook-secret").exists(), "nothing listens, so there is no secret"
    assert stat.S_IMODE((root / "team-kit").stat().st_mode) == 0o700
    assert yaml.safe_load((root / "team-kit" / "guard.yaml").read_text())["version"] == 2
    for name in ("team-kit-gh", "team-kit-admin", "team-kit-chief", "team-kit-slack"):
        wrapper = (root / "bin" / name).read_text()
        assert str(kit / "hermes" / "bin") in wrapper and "TEAM_KIT_HOME" in wrapper
    log = hermes_calls(root)
    assert ["-p", "default", "gateway", "install"] in log
    assert not any(c[2:4] == ["cron", "create"] for c in log), "the bootstrap bot has no jobs"
    assert not (root / "config.yaml").exists(), "the host profile's config is never touched"


def test_a_second_run_keeps_guard_yaml_and_bots_yaml(kit_source, machine):
    root, _ = machine
    assert install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay").returncode == 0
    guard = root / "team-kit" / "guard.yaml"
    guard.write_text(guard.read_text().replace("max_in_flight_total: 3", "max_in_flight_total: 5"))
    bots = (root / "team-kit" / "bots.yaml").read_text()
    (root / "profiles" / "ong-xay" / "SOUL.md").write_text("my own soul")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    assert "max_in_flight_total: 5" in guard.read_text()
    assert (root / "team-kit" / "bots.yaml").read_text() == bots
    assert (root / "profiles" / "ong-xay" / "SOUL.md.bak").read_text() == "my own soul"
    assert not any("gateway" in c for c in hermes_calls(root))


def test_refuses_a_version_1_guard_yaml(kit_source, machine):
    root, _ = machine
    (root / "team-kit").mkdir()
    (root / "team-kit" / "guard.yaml").write_text("version: 1\nrepos: [acme/shop]\n")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 1
    assert "Migrate from version 1" in done.stderr


@pytest.mark.parametrize("flag", ["--dispatcher-model", "--launcher-model"])
def test_version_1_flags_point_at_the_migration(machine, flag):
    _, env = machine
    done = subprocess.run(["bash", str(HERMES / "install.sh"), flag, "m"], env=env, capture_output=True, text=True)
    assert done.returncode == 1 and "Migrate from version 1" in done.stderr


def test_refuses_a_short_ref(machine):
    _, env = machine
    done = subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", "abc1234"], env=env, capture_output=True, text=True)
    assert done.returncode == 1
    assert "full 40-character commit" in done.stderr


def test_refuses_a_bad_bootstrap_bot_name(kit_source, machine):
    root, _ = machine
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "Ông Xày")
    assert done.returncode != 0 and "must be lowercase letters" in done.stderr
    assert not (root / "team-kit" / "bots.yaml").exists()


def test_the_bootstrap_bot_config_keeps_unrelated_settings_and_types(kit_source, machine):
    root, _ = machine
    seed_config(root, "ong-xay", {"plugins": {"enabled": ["other", "team_kit_guard"], "settings": {"other": {"x": 4}}},
                                  "approvals": {"custom": "keep"}, "model": {"default": "existing"}, "custom": [1, 2]})
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay")
    assert done.returncode == 0, done.stderr
    config = read_config(root, "ong-xay")
    assert config["plugins"] == {"enabled": ["other", "team_kit_guard"], "settings": {"other": {"x": 4}}}
    assert config["approvals"] == {"custom": "keep", "mode": "manual", "cron_mode": "deny"}
    assert config["model"] == {"default": "existing"} and config["custom"] == [1, 2]


def test_a_standalone_gateway_profile_is_not_silently_migrated(kit_source, machine):
    root, _ = machine
    original = {"gateway": {"standalone": True}, "model": "keep"}
    seed_config(root, "ong-xay", original)
    done = install(kit_source, machine, "--bootstrap-bot", "ong-xay")
    assert done.returncode != 0 and "gateway.standalone" in done.stderr
    assert read_config(root, "ong-xay") == original
    assert not any("gateway" in c for c in hermes_calls(root))


@pytest.mark.parametrize("failure", ["FAKE_CONFIG_FAIL", "FAKE_CONFIG_IGNORE"])
def test_a_config_cli_failure_is_detected(kit_source, machine, failure):
    root, env = machine
    env[failure] = "toolsets"
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay")
    assert done.returncode != 0
    assert "toolsets" in done.stderr
    assert not (root / "team-kit" / "bots.yaml").exists(), "bots.yaml is written only after the bot is set up"


@pytest.mark.parametrize("override", [False, True])
def test_shell_launcher_uses_validated_python_with_spaces(kit_source, machine, override):
    root, env = machine
    bin_dir = Path(env["PATH"].split(":")[0])
    fake = bin_dir / "fake-hermes"
    fake.write_text(FAKE_HERMES)
    fake.chmod(0o755)
    (bin_dir / "hermes").write_text('#!/bin/sh\nexec "' + str(fake) + '" "$@"\n')
    py = bin_dir / ("python with spaces" if override else "python3")
    py.write_text('#!/bin/sh\nexec "' + sys.executable + '" "$@"\n')
    py.chmod(0o755)
    if override:
        env["HERMES_PYTHON"] = str(py)
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    command = subprocess.run([str(root / "bin" / "team-kit-admin"), "status"], env=env, capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert json.loads(command.stdout)["bots"] == []


def test_invalid_explicit_python_fails_without_falling_back(kit_source, machine):
    root, env = machine
    env["HERMES_PYTHON"] = str(root / "missing-python")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0 and "HERMES_PYTHON" in done.stderr
    assert not (root / "team-kit").exists()


def test_no_usable_python_reports_actionable_error(kit_source, machine):
    root, env = machine
    bin_dir = Path(env["PATH"].split(":")[0])
    (bin_dir / "hermes").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "python3").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "python3").chmod(0o755)
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0 and "HERMES_PYTHON" in done.stderr and "PyYAML" in done.stderr
    assert not (root / "team-kit").exists()
