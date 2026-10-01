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

FAKE_HERMES = f"#!{sys.executable}\n" + (Path(__file__).with_name("fake_hermes.py")).read_text()


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
    env = {"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin", "TMPDIR": str(tmp_path)}
    return home / ".hermes", env


def install(kit_source, machine, *extra):
    src, sha = kit_source
    root, env = machine
    return subprocess.run(["bash", str(INSTALL), "--ref", sha, "--source", str(src), *extra], env=env, capture_output=True, text=True)


def calls(root):
    log = root / "calls.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


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
    host = yaml.safe_load((root / "config.yaml").read_text())
    route = host["platforms"]["webhook"]["extra"]["routes"]["team-kit-github"]
    assert route["profile"] == "dispatcher"
    assert any(c[:5] == ["-p", "dispatcher", "config", "set", "toolsets"] for c in calls(root))
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
    assert status[1:] == ["--name", "dispatch-status", "--deliver", "bot-chat:dispatcher", "--skill", "dispatch",
                          "--paused", "--paused-reason", "Awaiting repository allowlists, scoped credentials, and public webhook setup"]
    assert crons["every 2h"][1:3] == ["--name", "dispatcher-drain"]
    assert ["-p", "default", "gateway", "install"] in log
    jobs = json.loads((root / "profiles" / "dispatcher" / "jobs.json").read_text())
    assert len(jobs) == 2 and all(not job["enabled"] for job in jobs)
    assert all(job["paused_reason"] for job in jobs)
    assert secret not in done.stdout + done.stderr


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


def seed_config(root, profile, config):
    home = root if profile == "default" else root / "profiles" / profile
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.safe_dump(config))


def read_config(root, profile="default"):
    home = root if profile == "default" else root / "profiles" / profile
    return yaml.safe_load((home / "config.yaml").read_text())


def test_config_cli_preserves_unrelated_settings_and_types(kit_source, machine):
    root, _ = machine
    seed_config(root, "dispatcher", {"plugins": {"enabled": ["other", "team_kit_guard"], "settings": {"other": {"x": 4}}},
                                     "approvals": {"custom": "keep"}, "model": {"default": "existing"}, "custom": [1, 2]})
    seed_config(root, "default", {"gateway": {"custom": "keep"}, "platforms": {"telegram": {"enabled": True},
                                "webhook": {"enabled": False, "extra": {"host": "127.0.0.1", "port": 9999,
                                "secret": "existing-global-secret", "routes": {"other": {"prompt": "keep"}}}}}})
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    config = read_config(root, "dispatcher")
    assert config["plugins"] == {"enabled": ["other", "team_kit_guard"], "settings": {"other": {"x": 4}}}
    assert config["approvals"] == {"custom": "keep", "mode": "manual", "cron_mode": "deny"}
    assert config["model"] == {"default": "existing"} and config["custom"] == [1, 2]
    assert config["kanban"]["auto_decompose"] is False and isinstance(config["toolsets"], list)
    host = read_config(root)
    assert host["gateway"] == {"custom": "keep"} and host["platforms"]["telegram"] == {"enabled": True}
    webhook = host["platforms"]["webhook"]
    assert webhook["enabled"] is True
    assert {k: webhook["extra"][k] for k in ("host", "port", "secret")} == {
        "host": "127.0.0.1", "port": 9999, "secret": "existing-global-secret"}
    assert webhook["extra"]["routes"]["other"] == {"prompt": "keep"}
    assert any(c[:4] == ["-p", "default", "config", "set"] for c in calls(root))


@pytest.mark.parametrize("location", ["extra", "direct"])
def test_refuses_host_route_owned_by_someone_else(kit_source, machine, location):
    root, _ = machine
    routes = {"team-kit-github": {"profile": "other", "script": "other.py", "secret": "do-not-leak"}}
    webhook = {"extra": {"routes": routes}} if location == "extra" else {"routes": routes}
    original = {"platforms": {"webhook": webhook}}
    seed_config(root, "default", original)
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0
    assert "conflicting" in done.stderr and "team-kit-github" in done.stderr
    assert "do-not-leak" not in done.stderr + done.stdout
    assert read_config(root) == original
    assert not any("gateway" in c or "create" in c and "cron" in c for c in calls(root))


def test_reinstall_preserves_paused_and_active_job_state_and_matches_exact_names(kit_source, machine):
    root, _ = machine
    home = root / "profiles" / "dispatcher"
    home.mkdir(parents=True)
    original = [{"id": "one", "name": "dispatcher-drain-backup", "enabled": True},
                {"id": "two", "name": "dispatch-status", "enabled": True}]
    (home / "jobs.json").write_text(json.dumps(original))
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    jobs = json.loads((home / "jobs.json").read_text())
    assert jobs[:2] == original
    assert len(jobs) == 3 and jobs[2]["name"] == "dispatcher-drain" and not jobs[2]["enabled"]
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    assert json.loads((home / "jobs.json").read_text()) == jobs


@pytest.mark.parametrize("failure", ["FAKE_CONFIG_FAIL", "FAKE_CONFIG_IGNORE"])
def test_cli_failure_is_detected_without_exposing_secret(kit_source, machine, failure):
    root, env = machine
    env[failure] = "platforms.webhook.extra.routes"
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0
    secret = (root / "team-kit" / "webhook-secret").read_text().strip()
    assert secret not in done.stderr + done.stdout
    assert "platforms.webhook.extra.routes" in done.stderr
    assert not (root / "profiles" / "dispatcher" / "jobs.json").exists()


def test_cron_list_failure_does_not_create_duplicates(kit_source, machine):
    root, env = machine
    env["FAKE_CRON_LIST_FAIL"] = "1"
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0
    assert not any(c[2:4] == ["cron", "create"] for c in calls(root))


@pytest.mark.parametrize("override", [False, True])
def test_shell_launcher_uses_validated_python_with_spaces(kit_source, machine, override):
    root, env = machine
    bin_dir = Path(env["PATH"].split(":")[0])
    hermes = bin_dir / "hermes"
    fake = bin_dir / "fake-hermes"
    fake.write_text(FAKE_HERMES)
    fake.chmod(0o755)
    hermes.write_text('#!/bin/sh\nexec "' + str(fake) + '" "$@"\n')
    py = bin_dir / ("python with spaces" if override else "python3")
    py.write_text('#!/bin/sh\nexec "' + sys.executable + '" "$@"\n')
    py.chmod(0o755)
    if override:
        env["HERMES_PYTHON"] = str(py)
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    command = subprocess.run([str(root / "bin" / "team-kit-gh"), "kit-check", "--repo", "acme/nope"],
                             env=env, capture_output=True, text=True)
    assert command.returncode == 1 and "not in guard.yaml repos" in command.stderr


def test_invalid_explicit_python_fails_without_falling_back(kit_source, machine):
    root, env = machine
    env["HERMES_PYTHON"] = str(root / "missing-python")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0 and "HERMES_PYTHON" in done.stderr
    assert not (root / "profiles").exists()


def test_dynamic_route_is_not_shadowed_by_installer(kit_source, machine):
    root, _ = machine
    original = {"team-kit-github": {"secret": "private-dynamic-secret", "prompt": "unrelated"}}
    (root / "webhook_subscriptions.json").write_text(json.dumps(original))
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0 and "conflicting" in done.stderr
    assert "private-dynamic-secret" not in done.stdout + done.stderr
    assert json.loads((root / "webhook_subscriptions.json").read_text()) == original
    assert not (root / "config.yaml").exists()


def test_empty_webhook_secret_is_rejected(kit_source, machine):
    root, _ = machine
    (root / "team-kit").mkdir()
    (root / "team-kit" / "webhook-secret").write_text("\n")
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0 and "webhook-secret" in done.stderr
    assert not any("config" in c or "gateway" in c or "cron" in c for c in calls(root))


def test_standalone_dispatcher_is_not_silently_migrated(kit_source, machine):
    root, _ = machine
    original = {"gateway": {"standalone": True}, "model": "keep"}
    seed_config(root, "dispatcher", original)
    done = install(kit_source, machine)
    assert done.returncode != 0 and "gateway.standalone" in done.stderr
    assert read_config(root, "dispatcher") == original
    assert not any("gateway" in c for c in calls(root))


def test_no_usable_python_reports_actionable_error(kit_source, machine):
    root, env = machine
    bin_dir = Path(env["PATH"].split(":")[0])
    (bin_dir / "hermes").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "python3").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "python3").chmod(0o755)
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode != 0 and "HERMES_PYTHON" in done.stderr and "PyYAML" in done.stderr
    assert not (root / "profiles").exists()
