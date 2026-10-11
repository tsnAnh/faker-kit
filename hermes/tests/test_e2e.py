import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def load_e2e():
    path = Path(__file__).resolve().parents[1] / "e2e" / "run_dispatch_e2e.py"
    spec = importlib.util.spec_from_file_location("run_dispatch_e2e", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TOKEN = ["--token-env", "E2E_BOT_TOKEN"]
SECRET = "github_pat_SECRET0123456789"
SHA = "a" * 40
PR = "https://github.com/acme/scratch/pull/7"
READY_NOTICE = f"acme/scratch: {PR} is ready for you to merge. verified: unit-test-verified"


@pytest.fixture
def e2e():
    return load_e2e()


def test_argument_validation_requires_an_explicit_matching_scratch_repo(e2e):
    with pytest.raises(SystemExit):
        e2e.parse_args(["--repo", "acme/scratch", *TOKEN])
    with pytest.raises(SystemExit):
        e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/prod", *TOKEN])
    config = e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch", "--dry-run", *TOKEN])
    assert config.repo == "acme/scratch" and config.dry_run is True


def test_the_token_variable_is_named_on_the_command_line_and_is_not_the_operators(e2e):
    base = ["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch"]
    with pytest.raises(SystemExit):
        e2e.parse_args(base)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "not a name"):
        with pytest.raises(SystemExit):
            e2e.parse_args([*base, "--token-env", name])
    assert e2e.parse_args([*base, *TOKEN]).token_env == "E2E_BOT_TOKEN"


def test_backend_and_codex_env_are_gone(e2e):
    for flag in ("--backend", "--codex-env"):
        with pytest.raises(SystemExit):
            e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch", *TOKEN, flag, "claude"])


def test_runner_refuses_merge_commands(e2e):
    runner = e2e.CommandRunner(echo=False)
    with pytest.raises(e2e.E2EError, match="merge"):
        runner.run(["gh", "pr", "merge", "7"])
    with pytest.raises(e2e.E2EError, match="force"):
        runner.run(["git", "push", "--force", "origin", "feature"])


def test_stop_before_merge_checks_open_pr_without_merging(e2e, tmp_path):
    class FakeRunner:
        def __init__(self):
            self.calls = []

        def run(self, argv, **kwargs):
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps({"pr": 7, "state": "open", "merged": False, "head": "a" * 40, "checks": "passed"}), "")

    runner = FakeRunner()
    state = e2e.assert_pr_open_before_merge(
        runner,
        tmp_path / "team-kit-gh",
        {},
        "https://github.com/acme/scratch/pull/7",
    )
    assert state["merged"] is False
    assert runner.calls == [[str(tmp_path / "team-kit-gh"), "pr-state", "--pr", "7"]]
    assert all(argv[:3] != ["gh", "pr", "merge"] for argv in runner.calls)


class FakeGh:
    """Plays every command run_live runs: install.sh, team-kit-admin, team-kit-gh, and gh. Nothing touches a network.

    `ticks` is a list of notice lists, one per tick; the last repeats. `statuses` and `labels` work the same way.
    Every call is recorded with the environment it ran in.
    """

    def __init__(self, ticks=(), statuses=({"status": "running"},), labels=(("agent:running",),), pr_labels=("risk:low",), dispatch=None,
                 bot_login="meo-bot", launch_state="running"):
        self.ticks, self.statuses, self.labels, self.pr_label_names = list(ticks), list(statuses), list(labels), list(pr_labels)
        self.dispatch = dispatch or {"verifier_backend": "claude", "trusted_dispatchers": [bot_login, "owner"]}
        self.bot_login, self.launch_state = bot_login, launch_state
        self.calls = []
        self.tick_n = self.status_n = self.label_n = 0
        self.token_mode = None

    def run(self, argv, env=None, **kwargs):
        self.calls.append((list(argv), dict(env) if env is not None else None))
        name = Path(argv[0]).name
        out = self.dispatch_command(name, argv, env or {})
        return subprocess.CompletedProcess(argv, 0, out if isinstance(out, str) else json.dumps(out), "")

    def hide(self, secret):
        pass

    def nth(self, items, n):
        return items[min(n, len(items) - 1)]

    def dispatch_command(self, name, argv, env):
        if name == "git":
            return SHA + "\n"
        if name == "bash":  # install.sh creates the guard in the temporary TEAM_KIT_HOME
            home = Path(env["TEAM_KIT_HOME"])
            home.mkdir(parents=True)
            (home / "guard.yaml").write_text("version: 2\ntrusted_authors:\n  - OWNER-LOGIN\n")
            return ""
        if name == "team-kit-admin":
            (Path(env["HERMES_HOME"]).parent / argv[argv.index("--bot") + 1]).mkdir(parents=True)
            return {}
        if name == "gh":
            assert argv[1:3] == ["api", "user"]
            return ("owner" if env.get("GH_TOKEN") != SECRET else self.bot_login) + "\n"
        verb = argv[1]
        if verb == "kit-check":
            return {"kit": SHA, "kit_ref": SHA, "vendored": SHA}
        if verb == "show":
            return json.dumps(self.dispatch) if argv[3] == ".agents/dispatch.json" else "standing orders"
        if verb == "dispatch":
            return {"state": self.launch_state, "issue": 12, "why": "the andon cord is pulled"}
        if verb == "tick":
            notices = self.nth(self.ticks, self.tick_n) if self.ticks else []
            self.tick_n += 1
            return "\n".join(notices) or "[SILENT]"
        if verb == "report-status":
            status = self.nth(self.statuses, self.status_n)
            self.status_n += 1
            return status
        if verb == "pr-state":
            return {"pr": 7, "state": "open", "merged": False}
        if verb == "andon":
            return {"stopped": argv[2] == "raise"}
        raise AssertionError(f"unexpected command {argv}")


class FakeRunner(FakeGh):
    def dispatch_command(self, name, argv, env):
        if name == "gh" and argv[1] == "issue":
            labels = self.nth(self.labels, self.label_n)
            self.label_n += 1
            return {"labels": [{"name": n} for n in labels]}
        if name == "gh" and argv[1] == "pr":
            return {"labels": [{"name": n} for n in self.pr_label_names]}
        return super().dispatch_command(name, argv, env)


DONE = {"status": "done", "pr": PR}


class Clock:
    """A clock the fake sleep advances, so a timeout takes no real time."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def live(e2e, tmp_path, monkeypatch):
    monkeypatch.setenv("E2E_BOT_TOKEN", SECRET)
    monkeypatch.setenv("GH_TOKEN", "operator-broad-token")
    monkeypatch.setenv("GITHUB_TOKEN", "operator-other-token")

    def go(fake, *extra, capsys=None):
        config = e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch", *TOKEN, "--owner-login", "owner",
                                 "--timeout-minutes", "2", "--poll-seconds", "60", "--temp-parent", str(tmp_path), *extra])
        clock = Clock()
        return e2e.run_live(config, fake, sleep=clock.sleep, clock=clock)

    return go


def commands(fake, name):
    return [(argv, env) for argv, env in fake.calls if Path(argv[0]).name == name or (len(argv) > 1 and argv[1] == name)]


def test_a_complete_run_reports_only_what_it_saw_and_stops_before_merge(live, capsys, tmp_path):
    fake = FakeRunner(ticks=[[], [READY_NOTICE]], statuses=[{"status": "running"}, DONE], labels=[("agent:running",), ("agent:verifying",)])
    assert live(fake, "--keep-home") == 0
    out = capsys.readouterr().out
    for step in ("done report", "verifier launch", "verifier verdict", "ready-to-merge notice", "risk label", "andon cleared"):
        assert f"- {step}: observed" in out
    assert "not seen" not in out
    assert "risk:low" in out and PR in out
    assert all(argv[:3] != ["gh", "pr", "merge"] for argv, _ in fake.calls)
    assert not any(argv[:2] == ["git", "push"] for argv, _ in fake.calls)
    assert SECRET not in out


def test_home_is_left_alone_and_only_the_kit_and_hermes_homes_move(live, tmp_path):
    fake = FakeRunner(ticks=[[READY_NOTICE]], statuses=[DONE], labels=[("agent:verifying",)])
    real_home = os.environ["HOME"]
    assert live(fake, "--keep-home") == 0
    run_root = next(tmp_path.glob("team-kit-hermes-e2e-*"))
    for argv, env in fake.calls:
        if env is None:
            continue
        assert env["HOME"] == real_home, argv
        assert env["TEAM_KIT_HOME"] == str(run_root / ".hermes" / "team-kit")
        assert env["HERMES_HOME"].startswith(str(run_root / ".hermes"))
    assert str(run_root) != real_home


def test_the_project_bot_gets_its_own_token_and_never_the_operators(live, tmp_path, capsys):
    fake = FakeRunner(ticks=[[READY_NOTICE]], statuses=[DONE], labels=[("agent:verifying",)])
    assert live(fake, "--keep-home") == 0
    run_root = next(tmp_path.glob("team-kit-hermes-e2e-*"))
    env_file = run_root / ".hermes" / "profiles" / "hermes-e2e-project" / ".env"
    assert env_file.read_text() == f"GH_TOKEN={SECRET}\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    for argv, env in fake.calls:
        assert SECRET not in " ".join(argv), "a token is never an argument"
        if env is None:
            continue
        assert "E2E_BOT_TOKEN" not in env, "the variable that carried the token is not passed on"
        if Path(argv[0]).name == "team-kit-gh" or (argv[0] == "gh" and env.get("HERMES_HOME", "").endswith("hermes-e2e-project")):
            assert env["GH_TOKEN"] == SECRET
            assert "GITHUB_TOKEN" not in env
    bot_gh = [env for argv, env in fake.calls if argv[0] == "gh" and env and env["GH_TOKEN"] == SECRET]
    assert bot_gh, "the bot's own login was read with its own token"
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    guard = (run_root / ".hermes" / "team-kit" / "guard.yaml").read_text()
    assert "- meo-bot" in guard and "- owner" in guard


@pytest.mark.parametrize("value", [None, "", "two words", "line\nbreak"])
def test_it_refuses_to_run_without_a_usable_token_before_touching_anything(live, monkeypatch, tmp_path, value, e2e):
    if value is None:
        monkeypatch.delenv("E2E_BOT_TOKEN")
    else:
        monkeypatch.setenv("E2E_BOT_TOKEN", value)
    fake = FakeRunner()
    with pytest.raises(e2e.E2EError, match="E2E_BOT_TOKEN"):
        live(fake)
    assert fake.calls == []
    assert list(tmp_path.glob("team-kit-hermes-e2e-*")) == []


def test_command_output_never_carries_the_token(e2e):
    runner = e2e.CommandRunner(echo=False)
    runner.hide(SECRET)
    env = {**os.environ, "LEAK": SECRET}
    done = runner.run([sys.executable, "-c", "import os, sys; print(os.environ['LEAK']); print(os.environ['LEAK'], file=sys.stderr)"], env=env)
    assert SECRET not in done.stdout + done.stderr
    with pytest.raises(e2e.E2EError) as err:
        runner.run([sys.executable, "-c", "import os, sys; print(os.environ['LEAK'], file=sys.stderr); sys.exit(3)"], env=env)
    assert SECRET not in str(err.value) and "***" in str(err.value)


def test_a_token_login_missing_from_trusted_dispatchers_stops_the_run(live, e2e):
    fake = FakeRunner(dispatch={"verifier_backend": "claude", "trusted_dispatchers": ["someone-else"]})
    with pytest.raises(e2e.E2EError, match="trusted_dispatchers"):
        live(fake)


def test_a_launch_that_does_not_start_is_not_reported_as_launched(live, e2e, capsys):
    fake = FakeRunner(launch_state="gated")
    with pytest.raises(e2e.E2EError, match="did not launch"):
        live(fake)
    out = capsys.readouterr().out
    assert "- launch: not seen" in out and "- scratch repo kit preflight: observed" in out


def test_waiting_forever_for_the_ready_notice_times_out_and_says_what_was_not_seen(live, e2e, capsys):
    fake = FakeRunner()  # nothing ever happens
    with pytest.raises(e2e.E2EError, match="timed out after 2 minutes"):
        live(fake)
    out = capsys.readouterr().out
    assert "- launch: observed" in out
    for step in ("done report", "verifier launch", "verifier verdict", "ready-to-merge notice"):
        assert f"- {step}: not seen (waited up to 2 min)" in out
    assert "- risk label: not seen" in out
    assert fake.tick_n == 3, "polled at 0, 60, and 120 seconds, then gave up"


def test_a_done_report_without_a_verifier_launch_or_notice_times_out_on_the_rest(live, e2e, capsys):
    fake = FakeRunner(statuses=[DONE])
    with pytest.raises(e2e.E2EError, match="timed out"):
        live(fake)
    out = capsys.readouterr().out
    assert "- done report: observed" in out
    assert "- verifier launch: not seen" in out and "- ready-to-merge notice: not seen" in out


def test_a_verifier_launch_without_a_done_report_does_not_count_as_one(live, e2e, capsys):
    fake = FakeRunner(labels=[("agent:verifying",)])
    with pytest.raises(e2e.E2EError, match="timed out"):
        live(fake)
    out = capsys.readouterr().out
    assert "- verifier launch: observed" in out and "- done report: not seen" in out


def test_a_ready_notice_with_no_done_report_seen_fails_instead_of_claiming_one(live, e2e, capsys):
    fake = FakeRunner(ticks=[[READY_NOTICE]], labels=[("agent:verifying",)])
    with pytest.raises(e2e.E2EError, match="never saw: done report"):
        live(fake)
    out = capsys.readouterr().out
    assert "- done report: not seen" in out and "- verifier verdict: observed" in out


def test_a_missed_verifier_launch_is_reported_not_seen_and_does_not_fail_the_run(live, capsys):
    fake = FakeRunner(ticks=[[READY_NOTICE]], statuses=[DONE])
    assert live(fake) == 0
    out = capsys.readouterr().out
    assert "- verifier launch: not seen (waited up to 2 min)" in out
    assert "- done report: observed" in out


def test_a_ready_notice_without_a_verifier_verdict_fails(live, e2e):
    fake = FakeRunner(ticks=[[f"{PR} is ready for you to merge"]], statuses=[DONE])
    with pytest.raises(e2e.E2EError, match="no verifier verdict"):
        live(fake)


def test_a_gated_unit_stops_the_run(live, e2e):
    fake = FakeRunner(ticks=[["acme/scratch #12 needs you: a gate"]])
    with pytest.raises(e2e.E2EError, match="gated"):
        live(fake)


def test_a_pull_request_with_no_risk_label_fails_and_never_reports_one(live, e2e, capsys):
    fake = FakeRunner(ticks=[[READY_NOTICE]], statuses=[DONE], labels=[("agent:verifying",)], pr_labels=())
    with pytest.raises(e2e.E2EError, match="no risk label"):
        live(fake)
    out = capsys.readouterr().out
    assert "- risk label: not seen" in out and "- andon raised: not seen" in out


def test_wait_for_ready_polls_at_the_configured_cadence(e2e, tmp_path):
    config = e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch", *TOKEN, "--timeout-minutes", "3", "--poll-seconds", "30"])
    paths = e2e.Paths(root=tmp_path, run_root=tmp_path, hermes_root=tmp_path / ".hermes", team_home=tmp_path / ".hermes" / "team-kit",
                      token_env="E2E_BOT_TOKEN", project_token=SECRET)
    fake, clock, obs = FakeRunner(), Clock(), e2e.Observations(config.timeout_seconds)
    with pytest.raises(e2e.E2EError, match="timed out after 3 minutes"):
        e2e.wait_for_ready(fake, paths, config, 12, obs, sleep=clock.sleep, clock=clock)
    assert clock.now == 180 and obs.seen == {}
