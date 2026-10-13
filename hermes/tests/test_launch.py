import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from hashlib import sha256

import pytest
from conftest import FAKE_CLAUDE, HERMES, KIT
from team_kit_cloud import KEY_HELD, KEY_WAIT, Cloud, CloudError
from team_kit_contracts import LAUNCH_PROMPTS, CloudCommand, ContractError, cloud_argv, launch_argv, verify_argv

PREFIXES = ("/faker-mode ", "$faker-mode ")


@pytest.mark.parametrize("backend", sorted(LAUNCH_PROMPTS))
def test_every_launch_prompt_template_starts_with_faker_mode(backend):
    assert LAUNCH_PROMPTS[backend].startswith(PREFIXES)


CLAUDE = ("/faker-mode Work the brief in acme/shop#123 using the follow-brief skill. If the follow-brief skill is not available in this session, "
          "comment on the issue with `<!-- team-kit:report v1 -->` as the first line and `status: blocked` as the second, "
          "say the team-kit skills did not load, and stop.")
CODEX = ("$faker-mode Work the brief in acme/shop#123 using the follow-brief skill. "
         "If the follow-brief skill is not available in this session, stop and say the team-kit skills did not load.")

@pytest.mark.parametrize(
    ("backend", "size", "expected_argv", "prompt"),
    [
        ("claude", "large", ("claude", "--cloud"), CLAUDE),
        ("codex", "small", ("codex", "cloud", "exec", "--env", "env_7", "--attempts", "1", "-"), CODEX),
        ("codex", "medium", ("codex", "cloud", "exec", "--env", "env_7", "--attempts", "2", "-"), CODEX),
        ("codex", "large", ("codex", "cloud", "exec", "--env", "env_7", "--attempts", "3", "-"), CODEX),
    ],
)
def test_launch_argv_is_exact_and_keeps_prompt_out_of_argv(backend, size, expected_argv, prompt):
    command = launch_argv(backend, "acme/shop", 123, size, "env_7")
    assert command.argv == expected_argv
    assert command.prompt == prompt
    assert command.prompt.startswith(PREFIXES)
    assert command.prompt not in command.argv


def status_cloud(output, calls=None, error=None):
    def run_status(argv):
        if calls is not None:
            calls.append(argv)
        if error:
            raise error
        return output

    return Cloud(run_gh=no_clone, run_status=run_status)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("[PENDING] Verify acme/shop#123\nenv-1  \u2022  3s ago\nno diff\n", "pending"),
        ("[READY] Verify acme/shop#123\nEnv  \u2022  1m ago\n+5/-2 \u2022 3 files\n", "ready"),
        ("[APPLIED] Work the brief\nEnv  \u2022  1h ago\nno diff\n", "applied"),
        ("\x1b[31m[ERROR]\x1b[0m Work the brief\n", "error"),
        ("", "unknown"),
        ("Error: not signed in\n", "unknown"),
        ("run [READY] anything\n", "unknown"),
    ],
)
def test_task_status_reads_only_the_label_codex_cloud_status_prints(output, expected):
    assert status_cloud(output).task_status("codex", "task_abc123") == expected


def test_task_status_runs_exactly_codex_cloud_status_with_the_task_id():
    calls = []
    cloud = status_cloud("[READY] t\n", calls)
    assert cloud.task_status("codex", "task_abc123") == "ready"
    assert cloud.task_status("codex", "https://chatgpt.com/codex/tasks/task_e_9Zy-1?x=1#top") == "ready"
    assert calls == [["codex", "cloud", "status", "task_abc123"], ["codex", "cloud", "status", "task_e_9Zy-1"]]


@pytest.mark.parametrize(
    ("backend", "session"),
    [
        ("claude", "https://claude.ai/code/session_01ABCDEF"),
        ("codex", "https://claude.ai/code/session_01ABCDEF"),
        ("codex", "task_abc; rm -rf /"),
        ("codex", "--help"),
        ("codex", ""),
    ],
)
def test_task_status_never_calls_the_cli_for_anything_but_a_codex_task_id(backend, session):
    calls = []
    assert status_cloud("[READY] t\n", calls).task_status(backend, session) == "unknown"
    assert calls == []


@pytest.mark.parametrize("error", [FileNotFoundError("codex"), subprocess.TimeoutExpired("codex", 30), OSError("boom")])
def test_task_status_is_unknown_when_the_cli_cannot_be_run(error):
    assert status_cloud("", error=error).task_status("codex", "task_abc123") == "unknown"


def test_a_claude_session_without_the_kit_posts_a_report_tick_can_read():
    from team_kit_contracts import parse_report  # noqa: PLC0415

    prompt = launch_argv("claude", "acme/shop", 1, "small").prompt
    comment = "<!-- team-kit:report v1 -->\nstatus: blocked\nThe team-kit skills did not load."
    assert "`<!-- team-kit:report v1 -->` as the first line and `status: blocked` as the second" in prompt
    assert parse_report(comment).status == "blocked"


@pytest.mark.parametrize(("repo", "env", "size"), [("acme/shop;id", "e", "small"), ("acme", "e", "small"), ("acme/shop", "e;id", "small"), ("acme/shop", None, "small"), ("acme/shop", "e", "huge")])
def test_launch_argv_refuses_unsafe_parts(repo, env, size):
    with pytest.raises(ContractError):
        launch_argv("codex", repo, 1, size, env)


def test_cloud_argv_refuses_an_unknown_backend():
    with pytest.raises(ContractError):
        cloud_argv("gemini", "x")


def written_launch_prompts():
    """Every inline cloud prompt still written in the kit's dispatch prose."""
    files = [*HERMES.rglob("*.md"), *(KIT / "plugins" / "trust-ladder" / "skills").glob("*dispatch*/**/*.md"),
             *(KIT / "plugins" / "trust-ladder" / "skills" / "follow-brief").rglob("*.md"),
             KIT / "docs" / "how-to" / "dispatch-with-hermes.md", KIT / "plugins" / "trust-ladder" / "references" / "compat.md"]
    pattern = re.compile(r"(?:claude --cloud|codex cloud exec(?: --env \S+)?(?: --attempts \S+)?) (['\"])(.*?)\1")
    return [(f.relative_to(KIT), m.group(2)) for f in files for m in pattern.finditer(f.read_text())]


def test_no_written_launch_prompt_puts_prompt_text_in_argv():
    prompts = written_launch_prompts()
    assert prompts == []


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        # Each tick counts as a full second toward the deadline but waits only briefly.
        time.sleep(0.05)
        self.t += seconds


def fake_agent(script):
    return [sys.executable, "-c", script]


def command(argv, prompt="prompt", sensitive=()):
    return CloudCommand(tuple(argv), prompt, tuple(sensitive))


def no_clone(args, input=None):
    raise AssertionError(f"unexpected gh call {args}")


def cloud(wait=200.0, run_gh=no_clone, now=time.time):
    clock = Clock()
    return Cloud(run_gh=run_gh, wait_seconds=wait, sleep=clock.sleep, clock=clock, now=now)


def test_codex_starts_in_an_empty_directory(tmp_path):
    workdir = tmp_path / "w"
    workdir.mkdir()
    (workdir / "stale").write_text("x")
    argv = fake_agent("import os; print('files', os.listdir('.')); print('task_e1b2c3d4e5')")
    assert cloud().start("codex", "acme/shop", command(argv), workdir) == "task_e1b2c3d4e5"
    assert "files []" in (tmp_path / "w.log").read_text()


def test_codex_prompt_is_sent_on_stdin_not_argv_or_logs(tmp_path):
    key = "0" * 32
    prompt = f"$verify-unit Sign the verdict with key {key}."
    script = ("import hashlib, sys; data = sys.stdin.read(); print('argv', sys.argv[1:]); "
              "print('digest', hashlib.sha256(data.encode()).hexdigest()); print('task_e1b2c3d4e5')")
    workdir = tmp_path / "w"
    assert cloud().start("codex", "acme/shop", command([*fake_agent(script), "-"], prompt), workdir) == "task_e1b2c3d4e5"
    log = (tmp_path / "w.log").read_text()
    assert "argv ['-']" in log
    assert f"digest {sha256(prompt.encode()).hexdigest()}" in log
    assert key not in log


def test_echoed_signing_prompt_is_redacted_from_log_and_error(tmp_path):
    key = "0123456789abcdef" * 2
    private_command = verify_argv(
        "codex",
        "acme/shop",
        7,
        "https://github.com/acme/shop/pull/7",
        "a" * 40,
        key,
        "env_7",
    )
    assert private_command.sensitive == (key,)
    script = ("import sys; data = sys.stdin.read(); key = data.split('with key ', 1)[1].split('.', 1)[0]; "
              "print('\\x1b[1misolated key:', key); print('backend rejected prompt:', data); sys.exit(3)")
    private_command = replace(private_command, argv=tuple(fake_agent(script)))
    workdir = tmp_path / "checkouts" / "acme__shop" / "7"

    with pytest.raises(CloudError) as raised:
        cloud().start("codex", "acme/shop", private_command, workdir)

    log = workdir.with_name("7.log").read_text()
    error = str(raised.value)
    assert "isolated key: [redacted signing key]" in log
    assert "backend rejected prompt: [redacted prompt]" in log
    assert "backend rejected prompt: [redacted prompt]" in error
    assert key not in log
    assert private_command.prompt not in log
    assert key not in error
    assert private_command.prompt not in error


def test_an_agent_that_exits_without_a_session_is_an_error(tmp_path):
    argv = fake_agent("import sys; print('not signed in'); sys.exit(3)")
    with pytest.raises(CloudError, match="exited 3 before starting a session: not signed in"):
        cloud().start("codex", "acme/shop", command(argv), tmp_path / "w")


def test_an_agent_that_prints_no_session_in_time_is_an_error(tmp_path):
    argv = fake_agent("import time; time.sleep(30)")
    with pytest.raises(CloudError, match="no session in 2s"):
        cloud(wait=2.0).start("codex", "acme/shop", command(argv), tmp_path / "w")


def test_the_pseudo_terminal_is_the_childs_controlling_terminal_so_dev_tty_opens(tmp_path):
    script = ("import os; fd = os.open('/dev/tty', os.O_RDONLY); "
              "print('tty', os.tcgetpgrp(fd) == os.tcgetpgrp(1), 'stdin_tty', os.isatty(0), 'sid', os.getsid(0) == os.getpid()); "
              "print('task_e1b2c3d4e5', flush=True)")
    assert cloud().start("codex", "acme/shop", command(fake_agent(script)), tmp_path / "w") == "task_e1b2c3d4e5"
    assert "tty True stdin_tty False" in (tmp_path / "w.log").read_text()


def test_ci_is_not_passed_to_the_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    script = "import os; print('ci', repr(os.environ.get('CI'))); print('task_e1b2c3d4e5', flush=True)"
    cloud().start("codex", "acme/shop", command(fake_agent(script)), tmp_path / "w")
    assert "ci None" in (tmp_path / "w.log").read_text()


def test_the_child_survives_until_the_session_exists_is_reaped_and_leaks_no_descriptors(tmp_path):

    fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"  # noqa: PTH112
    before = len(os.listdir(fd_dir))  # noqa: PTH208
    script = ("import time; time.sleep(0.5); print('task_e1b2c3d4e5', flush=True); time.sleep(0.3)")
    procs = []

    def spy(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        procs.append(proc)
        return proc

    runner = cloud()
    runner.popen = spy
    assert runner.start("codex", "acme/shop", command(fake_agent(script)), tmp_path / "w") == "task_e1b2c3d4e5"
    deadline = time.time() + 10
    while procs[0].returncode is None and time.time() < deadline:
        time.sleep(0.05)
    assert procs[0].returncode == 0, "the drain thread reaps the child after it exits"
    deadline = time.time() + 5
    while len(os.listdir(fd_dir)) > before and time.time() < deadline:  # noqa: PTH208
        time.sleep(0.05)
    assert len(os.listdir(fd_dir)) <= before  # noqa: PTH208


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def claude_cli(tmp_path, monkeypatch):
    """A fake claude on disk, an origin repository, and a gh that clones it; returns (cli, calls, origin, clones)."""
    cli = tmp_path / "bin" / "claude"
    cli.parent.mkdir()
    cli.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE)
    cli.chmod(0o755)
    calls = tmp_path / "claude-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))
    origin = tmp_path / "origin"
    git("init", "-q", "-b", "main", str(origin))
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "one", cwd=origin)
    clones = []

    def run_gh(args, input=None):
        assert args[:3] == ["repo", "clone", "acme/shop"]
        clones.append(args)
        git("clone", "-q", str(origin), args[3])
        return ""

    return cli, calls, origin, run_gh, clones


def claude_calls(calls):
    return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


def test_a_claude_session_starts_with_its_task_and_gets_its_signing_key_only_on_stdin(tmp_path, claude_cli):
    cli, calls, _, run_gh, _ = claude_cli
    key = "0123456789abcdef" * 2
    private = verify_argv("claude", "acme/shop", 7, "https://github.com/acme/shop/pull/7", "a" * 40, key)
    workdir = tmp_path / "checkouts" / "acme__shop" / "7-verify"
    session = cloud(run_gh=run_gh).start("claude", "acme/shop", replace(private, argv=(str(cli), "--cloud")), workdir)
    assert session == "https://claude.ai/code/session_01FAKEabc"
    create, send = claude_calls(calls)
    checkout = str(workdir.parent / "repo")
    assert create == {"kind": "create", "cwd": checkout, "argv": ["--cloud", private.prompt.replace(key, KEY_HELD) + KEY_WAIT]}
    assert create["argv"][1].endswith("reply only: waiting for the signing key. Then do the task above."), "it waits for the key"
    assert send == {"kind": "send", "argv": ["--cloud", "session_01FAKEabc", "-p"], "stdin": f"The signing key for the task above: {key}",
                    "cwd": checkout}
    assert key in private.prompt and not any(key in arg for call in (create, send) for arg in call["argv"])
    assert key not in (workdir.parent / "7-verify.log").read_text()


@pytest.mark.parametrize("prompt", ["", "--dangerously-skip-permissions"])
def test_a_claude_task_that_is_empty_or_looks_like_an_option_is_refused_before_anything_runs(tmp_path, claude_cli, prompt):
    cli, calls, _, run_gh, _ = claude_cli
    with pytest.raises(CloudError, match="must be text that does not start with -"):
        cloud(run_gh=run_gh).start("claude", "acme/shop", command([str(cli), "--cloud"], prompt), tmp_path / "checkouts" / "acme__shop" / "5")
    assert claude_calls(calls) == []


def test_a_coding_unit_session_gets_its_task_as_the_only_message(tmp_path, claude_cli):
    cli, calls, _, run_gh, _ = claude_cli
    unit = launch_argv("claude", "acme/shop", 12, "large")
    workdir = tmp_path / "checkouts" / "acme__shop" / "12"
    cloud(run_gh=run_gh).start("claude", "acme/shop", replace(unit, argv=(str(cli), "--cloud")), workdir)
    assert claude_calls(calls) == [{"kind": "create", "cwd": str(workdir.parent / "repo"), "argv": ["--cloud", unit.prompt]}]
    assert unit.prompt.startswith("/faker-mode Work the brief in acme/shop#12")


def test_the_launch_checkout_is_cloned_once_kept_through_pruning_and_moved_to_the_default_branch_tip(tmp_path, claude_cli):
    cli, _, origin, run_gh, clones = claude_cli
    parent = tmp_path / "checkouts" / "acme__shop"
    command_ = command([str(cli), "--cloud"], "/faker-mode Work the brief")
    cloud(run_gh=run_gh).start("claude", "acme/shop", command_, parent / "1")
    git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "two", cwd=origin)
    later = cloud(run_gh=run_gh, now=lambda: time.time() + 10 * 24 * 3600)  # everything else in parent is past KEEP_SECONDS
    later.start("claude", "acme/shop", command_, parent / "2")
    assert len(clones) == 1
    assert git("rev-parse", "HEAD", cwd=parent / "repo") == git("rev-parse", "HEAD", cwd=origin)
    assert not (parent / "1.log").exists(), "old logs are still pruned"


def test_a_trust_question_stops_the_launch_with_how_to_answer_it_and_sends_nothing(tmp_path, claude_cli, monkeypatch):
    cli, calls, _, run_gh, _ = claude_cli
    monkeypatch.setenv("FAKE_CLAUDE_TRUST", "1")
    workdir = tmp_path / "checkouts" / "acme__shop" / "3"
    with pytest.raises(CloudError) as raised:
        cloud(run_gh=run_gh).start("claude", "acme/shop", command([str(cli), "--cloud"], "secret prompt"), workdir)
    assert f"trust it once: cd {workdir.parent / 'repo'} && claude" in str(raised.value)
    assert [c["kind"] for c in claude_calls(calls)] == ["create"]


def test_a_session_that_did_not_get_its_task_is_an_error_without_the_prompt_or_key(tmp_path, claude_cli, monkeypatch):
    cli, _, _, run_gh, _ = claude_cli
    monkeypatch.setenv("FAKE_CLAUDE_SEND_FAIL", "1")
    key = "fedcba9876543210" * 2
    with pytest.raises(CloudError) as raised:
        cloud(run_gh=run_gh).start("claude", "acme/shop", command([str(cli), "--cloud"], f"secret prompt with key {key}", [key]),
                                   tmp_path / "checkouts" / "acme__shop" / "4")
    error = str(raised.value)
    assert error.startswith("https://claude.ai/code/session_01FAKEabc started but did not get its signing key")
    assert "[redacted signing key]" in error, "the CLI's own output repeated the key, and it was redacted"
    assert key not in error and "secret prompt" not in error
