import hashlib
import json
import os
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


def test_a_folder_a_deleted_profile_left_behind_is_created_again(kit_source, machine):
    root, _ = machine
    ghost = root / "profiles" / "ong-xay" / "runtime"
    ghost.mkdir(parents=True)  # what Hermes leaves when it deletes a profile: no identity, and every command refused
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay")
    assert done.returncode == 0, done.stderr
    assert ["-p", "default", "profile", "create", "ong-xay"] in hermes_calls(root)
    assert read_config(root, "ong-xay")["toolsets"] == ["terminal", "clarify", "skills"]


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
    for name in ("team-kit-gh", "team-kit-admin", "team-kit-chief", "team-kit-slack", "team-kit-runner"):
        wrapper = (root / "bin" / name).read_text()
        assert str(kit / "hermes" / "bin") in wrapper and "TEAM_KIT_HOME" in wrapper
    log = hermes_calls(root)
    assert ["-p", "default", "gateway", "install"] in log
    created = [c for c in log if c[2:4] == ["cron", "create"]]
    assert [(c[1], c[c.index("--name") + 1]) for c in created] == [("ong-xay", "team-kit-inbox")], "the bootstrap bot's one job"
    assert "--paused" not in created[0] and "--no-agent" in created[0], "it needs no token, so it starts active, and runs no model"
    inbox = (home / "scripts" / "team-kit-jobs" / "team-kit-inbox.sh").read_text()
    assert f"{root / 'bin' / 'team-kit-admin'} inbox" in inbox
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
    assert done.returncode != 0 and "HERMES_PYTHON" in done.stderr and "Python 3.9+" in done.stderr
    assert not (root / "team-kit").exists()


def test_a_python_without_pyyaml_runs_every_command_with_the_vendored_copy(kit_source, machine):
    root, env = machine
    bin_dir = Path(env["PATH"].split(":")[0])
    # -S leaves out site-packages, so this Python has only the standard library: no PyYAML.
    bare = bin_dir / "bare-python"
    bare.write_text('#!/bin/sh\nexec "' + sys.executable + '" -S "$@"\n')
    bare.chmod(0o755)
    assert subprocess.run([str(bare), "-c", "import yaml"], capture_output=True).returncode != 0
    env["HERMES_PYTHON"] = str(bare)
    done = install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay")
    assert done.returncode == 0, done.stderr
    for name in ("admin", "gh", "chief", "slack"):
        assert f"py='{bare}'" in (root / "bin" / f"team-kit-{name}").read_text()
    command = subprocess.run([str(root / "bin" / "team-kit-admin"), "status"], env=env, capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert {"bot": "ong-xay", "role": "bootstrap", "project_requests": []} in json.loads(command.stdout)["bots"], "it read bots.yaml and guard.yaml"


def test_a_wrapper_whose_python_was_removed_falls_back_to_python3_on_path(kit_source, machine):
    root, env = machine
    bin_dir = Path(env["PATH"].split(":")[0])
    gone = bin_dir / "python-to-remove"
    gone.write_text('#!/bin/sh\nexec "' + sys.executable + '" "$@"\n')
    gone.chmod(0o755)
    env["HERMES_PYTHON"] = str(gone)
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    gone.unlink()  # what an upgrade that removes the old interpreter leaves
    (bin_dir / "python3").write_text('#!/bin/sh\nexec "' + sys.executable + '" "$@"\n')
    (bin_dir / "python3").chmod(0o755)
    command = subprocess.run([str(root / "bin" / "team-kit-admin"), "status"], env=env, capture_output=True, text=True)
    assert command.returncode == 0, command.stderr
    assert json.loads(command.stdout)["bots"] == []


def test_a_team_kit_home_elsewhere_moves_bin_and_profiles_with_it_and_leaves_home_alone(kit_source, machine, tmp_path):
    root, env = machine
    elsewhere = tmp_path / "scratch" / ".hermes"
    env = {**env, "TEAM_KIT_HOME": str(elsewhere / "team-kit"), "HERMES_HOME": str(elsewhere)}
    src, sha = kit_source
    done = subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", sha, "--source", str(src), "--no-gateway", "--bootstrap-bot", "ong-xay"],
                          env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in (elsewhere / "bin").iterdir()) == WRAPPERS
    assert f"export TEAM_KIT_HOME='{elsewhere / 'team-kit'}'\n" in (elsewhere / "bin" / "faker-kit-gh").read_text()
    assert (elsewhere / "team-kit" / "kit").is_dir()
    assert not (root / "bin").exists() and not (root / "team-kit").exists(), "the real ~/.hermes is untouched"


FAKER_KIT_URL = "https://github.com/tsnAnh/faker-kit.git"
TEAM_KIT_URL = "https://github.com/tsnAnh/team-kit.git"  # legacy team-kit URL; install must never fall back to it
WRAPPERS = sorted(f"{prefix}-{name}" for prefix in ("faker-kit", "team-kit") for name in ("gh", "admin", "chief", "slack", "runner"))


def with_urls(env, tmp_path, mapping):
    """Git rewrites each URL to a local path, so the default --source is exercised without the network."""
    config = tmp_path / "gitconfig"
    config.write_text("".join(f'[url "{target}"]\n\tinsteadOf = {url}\n' for url, target in mapping.items()))
    return {**env, "GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}


def install_default_source(kit_source, env, *extra):
    return subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", kit_source[1], *extra], env=env, capture_output=True, text=True)


def snapshot(*files):
    return {str(f): f.read_bytes() for f in files}


def team_kit_state(root):
    """Every file under team-kit/ that install.sh does not own: the kit checkout and its skill links are excluded."""
    home = root / "team-kit"
    return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(home.rglob("*"))
            if p.is_file() and p.relative_to(home).parts[0] not in ("kit", "all-skills")}


def test_a_fresh_install_fetches_the_faker_kit_source_by_default(kit_source, machine, tmp_path):
    root, env = machine
    src, sha = kit_source
    done = install_default_source(kit_source, with_urls(env, tmp_path, {FAKER_KIT_URL: src}), "--no-gateway")
    assert done.returncode == 0, done.stderr
    assert git(root / "team-kit" / "kit", "rev-parse", "HEAD") == sha
    assert sorted(p.name for p in (root / "bin").iterdir()) == WRAPPERS
    for name in WRAPPERS:
        assert os.access(root / "bin" / name, os.X_OK), name
    py = (root / "bin" / "team-kit-gh").read_text().splitlines()[2].removeprefix("py='").removesuffix("'")
    kit_script = root / "team-kit" / "kit" / "hermes" / "bin" / "team_kit_gh.py"
    assert (root / "bin" / "faker-kit-gh").read_text() == (
        "#!/usr/bin/env bash\n"
        f"export TEAM_KIT_HOME='{root / 'team-kit'}'\n"
        f"py='{py}'\n"
        '[ -x "$py" ] || py=$(command -v python3) || { echo "faker-kit-gh: no python3 to run it" >&2; exit 1; }\n'
        f"exec \"$py\" '{kit_script}' \"$@\"\n"
    )
    assert done.stdout.splitlines()[-2] == f"faker-kit Hermes module installed at kit {sha}. The team-kit-* commands remain, as the same commands."


def test_the_team_kit_url_is_not_a_fallback_for_the_default_source(kit_source, machine, tmp_path):
    root, env = machine
    src, _ = kit_source
    env = with_urls(env, tmp_path, {TEAM_KIT_URL: src, FAKER_KIT_URL: tmp_path / "no-such-kit"})
    done = install_default_source(kit_source, env, "--no-gateway")
    assert done.returncode != 0
    assert "no-such-kit" in done.stderr and "fatal:" in done.stderr, done.stderr
    assert list((root / "bin").iterdir()) == []
    assert not (root / "team-kit" / "guard.yaml").exists()


@pytest.fixture
def legacy(kit_source, machine):
    """An install as an older kit left it: only the team-kit-* wrappers, a bootstrap bot, a project bot whose jobs
    were activated, and the state a running machine accumulates."""
    root, env = machine
    assert install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay").returncode == 0
    (root / "team-kit" / "guard.yaml").write_text((root / "team-kit" / "guard.yaml").read_text().replace("  - OWNER-LOGIN", "  - Codertocat"))
    added = subprocess.run([str(root / "bin" / "team-kit-admin"), "add-project", "--bot", "meo-beo", "--project", "shop", "--repo", "acme/shop"],
                           env={**env, "HERMES_HOME": str(root / "profiles" / "ong-xay")}, capture_output=True, text=True)
    assert added.returncode == 0, added.stderr
    for name in ("gh", "admin", "chief", "slack"):
        (root / "bin" / f"faker-kit-{name}").unlink()
    for name in ("faker-kit-runner", "team-kit-runner"):  # an older kit had no runner command
        (root / "bin" / name).unlink()
    jobs = root / "profiles" / "meo-beo" / "jobs.json"
    jobs.write_text(json.dumps([{**job, "enabled": job["name"] != "team-kit-upstream", "state": "active"} for job in json.loads(jobs.read_text())]))
    home = root / "team-kit"
    for path, text in (
        ("units/meo-beo.json", '{"at": 1.0, "repo": "acme/shop", "units": {"7": {"state": "open"}}, "wants": []}'),
        ("in-flight/meo-beo.json", '{"7": 1700000000.0}'),
        ("andon/meo-beo.json", '{"raised_at": 1.0, "why": "prod is down"}'),
        ("checkouts/acme__shop/setup/README.md", "checkout\n"),
        ("briefs/meo-beo/t_1.md", "# brief\n"),
    ):
        (home / path).parent.mkdir(parents=True, exist_ok=True)
        (home / path).write_text(text)
    env_file = root / "profiles" / "meo-beo" / ".env"
    env_file.write_text(env_file.read_text() + "GH_TOKEN=ghp_fake0123456789\n" if env_file.exists() else "GH_TOKEN=ghp_fake0123456789\n")
    profile_state = [jobs, env_file]
    return root, env, profile_state


def test_upgrading_an_older_install_adds_the_faker_kit_names_and_keeps_every_state_file(kit_source, machine, legacy):
    root, env, profile_state = legacy
    state, profiles = team_kit_state(root), snapshot(*profile_state)
    assert {"andon/meo-beo.json", "bots.yaml", "briefs/meo-beo/t_1.md", "checkouts/acme__shop/repo/.git/HEAD", "checkouts/acme__shop/setup/README.md",
            "guard.yaml", "in-flight/meo-beo.json", "units/meo-beo.json"} <= set(state)
    assert sorted(p.name for p in (root / "bin").iterdir()) == [f"team-kit-{n}" for n in ("admin", "chief", "gh", "slack")], "an older kit had four"

    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in (root / "bin").iterdir()) == WRAPPERS
    assert team_kit_state(root) == state
    assert snapshot(*profile_state) == profiles
    assert [(job["id"], job["enabled"]) for job in json.loads(profile_state[0].read_text())] == [
        ("job-0", True), ("job-1", True), ("job-2", True), ("job-3", True), ("job-4", False)]
    for name in ("gh", "admin", "chief", "slack"):
        assert (root / "bin" / f"faker-kit-{name}").read_text() == (root / "bin" / f"team-kit-{name}").read_text()

    wrappers = snapshot(*((root / "bin" / name) for name in WRAPPERS))
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    assert team_kit_state(root) == state, "a repeated upgrade changes nothing"
    assert snapshot(*profile_state) == profiles
    assert snapshot(*((root / "bin" / name) for name in WRAPPERS)) == wrappers

    outputs = [subprocess.run([str(root / "bin" / f"{prefix}-admin"), "status"], env=env, capture_output=True, text=True) for prefix in ("faker-kit", "team-kit")]
    assert [o.returncode for o in outputs] == [0, 0], [o.stderr for o in outputs]
    assert json.loads(outputs[0].stdout) == json.loads(outputs[1].stdout)
    assert [b["bot"] for b in json.loads(outputs[0].stdout)["bots"]] == ["meo-beo", "ong-xay"]


@pytest.mark.parametrize("kind", ["script", "symlink", "directory", "other kit command"])
def test_a_file_the_kit_did_not_write_at_a_wrapper_path_stops_the_install(kit_source, machine, kind):
    root, _ = machine
    assert install(kit_source, machine, "--no-gateway").returncode == 0
    (root / "bin" / "faker-kit-gh").unlink()
    foreign = root / "bin" / "faker-kit-gh"
    if kind == "script":
        foreign.write_text("#!/bin/sh\necho my own faker-kit-gh\n")
    elif kind == "symlink":
        foreign.symlink_to(root / "bin" / "team-kit-gh")
    elif kind == "directory":
        foreign.mkdir()
    else:
        foreign.write_text((root / "bin" / "team-kit-admin").read_text())
    before = snapshot(*((root / "bin" / f"team-kit-{name}") for name in ("gh", "admin", "chief", "slack")), root / "team-kit" / "guard.yaml")
    owned = None if kind == "directory" else foreign.read_text()
    done = install(kit_source, machine, "--no-gateway")
    assert done.returncode == 1
    assert done.stderr == f"install.sh: {foreign} exists and is not a kit wrapper; move it aside, then run install.sh again\n"
    assert snapshot(*(Path(p) for p in before)) == before
    assert (foreign.is_symlink(), foreign.is_dir()) == {"symlink": (True, False), "directory": (False, True)}.get(kind, (False, False))
    assert (None if kind == "directory" else foreign.read_text()) == owned


def test_a_source_that_fails_to_fetch_leaves_the_install_as_it_was(kit_source, machine, tmp_path):
    root, env = machine
    assert install(kit_source, machine, "--no-gateway", "--bootstrap-bot", "ong-xay").returncode == 0
    kept = snapshot(*((root / "bin" / name) for name in WRAPPERS), root / "team-kit" / "guard.yaml", root / "team-kit" / "bots.yaml")
    done = subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", kit_source[1], "--source", str(tmp_path / "no-such-kit"), "--no-gateway"],
                          env=env, capture_output=True, text=True)
    assert done.returncode != 0
    assert "fatal:" in done.stderr and "no-such-kit" in done.stderr, done.stderr
    assert snapshot(*(Path(p) for p in kept)) == kept


def test_a_non_ascii_kit_home_reinstalls_under_any_locale(kit_source, machine, tmp_path):
    """launchd and cron run install.sh without LANG. The wrappers it writes there must be the ones it recognises later."""
    _, env = machine
    elsewhere = tmp_path / "Thư mục café" / ".hermes"
    src, sha = kit_source
    runs = {}
    for run, locale in enumerate(("C", "C.UTF-8", "C")):
        done = subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", sha, "--source", str(src), "--no-gateway"],
                              env={**env, "LC_ALL": locale, "TEAM_KIT_HOME": str(elsewhere / "team-kit"), "HERMES_HOME": str(elsewhere)},
                              capture_output=True, text=True)
        assert done.returncode == 0, (run, locale, done.stderr)
        runs[run] = snapshot(*((elsewhere / "bin" / name) for name in WRAPPERS))
    assert runs[0] == runs[1] == runs[2], "the wrappers do not depend on the locale"
    outputs = [subprocess.run([str(elsewhere / "bin" / f"{prefix}-admin"), "status"], env={**env, "LC_ALL": "C"}, capture_output=True, text=True)
               for prefix in ("faker-kit", "team-kit")]
    assert [o.returncode for o in outputs] == [0, 0], [o.stderr for o in outputs]
    assert json.loads(outputs[0].stdout) == json.loads(outputs[1].stdout)


@pytest.mark.parametrize(("folder", "quoting"), [("café", "$'"), ("my kit", "\\ "), ("kit", "/kit/hermes/bin/team_kit_gh.py \"")])
def test_a_wrapper_an_earlier_install_quoted_with_printf_q_is_still_a_kit_wrapper(kit_source, machine, tmp_path, folder, quoting):
    """Older kits quoted with printf %q: bare, backslash-escaped, or, for a non-ASCII path in the C locale, $'...'."""
    _, env = machine
    elsewhere = tmp_path / folder / ".hermes"
    kit_home = elsewhere / "team-kit"
    src, sha = kit_source
    (elsewhere / "bin").mkdir(parents=True)
    older = r'printf "#!/usr/bin/env bash\nexport TEAM_KIT_HOME=%q\nexec %q %q \"\$@\"\n" "$1" /usr/bin/python3 "$1/kit/hermes/bin/team_kit_gh.py"'
    written = subprocess.run(["bash", "-c", older, "older", str(kit_home)], env={**env, "LC_ALL": "C"}, capture_output=True, text=True, check=True).stdout
    assert quoting in written
    (elsewhere / "bin" / "team-kit-gh").write_text(written)
    done = subprocess.run(["bash", str(HERMES / "install.sh"), "--ref", sha, "--source", str(src), "--no-gateway"],
                          env={**env, "LC_ALL": "C", "TEAM_KIT_HOME": str(kit_home), "HERMES_HOME": str(elsewhere)}, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (elsewhere / "bin" / "team-kit-gh").read_text() == (elsewhere / "bin" / "faker-kit-gh").read_text()
