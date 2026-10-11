import re
import subprocess
import sys
import time
from dataclasses import replace
from hashlib import sha256

import pytest
from conftest import HERMES, KIT
from team_kit_cloud import Cloud, CloudError
from team_kit_contracts import CloudCommand, LAUNCH_PROMPTS, ContractError, cloud_argv, launch_argv, verify_argv

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

    return Cloud(run_gh=lambda *a, **k: "", run_status=run_status)


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
    from team_kit_contracts import parse_report

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


def cloud(gh_calls, wait=200.0):
    clock = Clock()

    def run_gh(args, input=None):
        gh_calls.append(args)
        if args[:2] == ["repo", "clone"]:
            subprocess.run(["git", "init", "-q", args[3]], check=True)
        return ""

    return Cloud(run_gh=run_gh, wait_seconds=wait, sleep=clock.sleep, clock=clock)


def test_claude_starts_from_a_fresh_clone_of_its_repository_and_returns_the_session(tmp_path):
    calls = []
    argv = fake_agent("import os; print('cwd', os.getcwd()); print('Session: https://claude.ai/code/session_01ABCdef', flush=True)")
    workdir = tmp_path / "checkouts" / "acme__shop" / "7"
    workdir.mkdir(parents=True)
    (workdir / "stale").write_text("x")
    assert cloud(calls).start("claude", "acme/shop", command(argv), workdir) == "https://claude.ai/code/session_01ABCdef"
    assert calls == [["repo", "clone", "acme/shop", str(workdir), "--", "--depth", "1", "--quiet"]]
    assert not (workdir / "stale").exists()
    assert f"cwd {workdir}" in (workdir.parent / "7.log").read_text()


def test_codex_starts_in_an_empty_directory_without_cloning(tmp_path):
    calls = []
    argv = fake_agent("print('task_e1b2c3d4e5')")
    assert cloud(calls).start("codex", "acme/shop", command(argv), tmp_path / "w") == "task_e1b2c3d4e5"
    assert calls == []


def test_codex_prompt_is_sent_on_stdin_not_argv_or_logs(tmp_path):
    key = "0" * 32
    prompt = f"$verify-unit Sign the verdict with key {key}."
    script = ("import hashlib, sys; data = sys.stdin.read(); print('argv', sys.argv[1:]); "
              "print('digest', hashlib.sha256(data.encode()).hexdigest()); print('task_e1b2c3d4e5')")
    workdir = tmp_path / "w"
    assert cloud([]).start("codex", "acme/shop", command([*fake_agent(script), "-"], prompt), workdir) == "task_e1b2c3d4e5"
    log = (tmp_path / "w.log").read_text()
    assert "argv ['-']" in log
    assert f"digest {sha256(prompt.encode()).hexdigest()}" in log
    assert key not in log


FAKE_CLAUDE_CLOUD = """
import os, sys
if sys.argv[1:] != ['--cloud']:
    print('bad argv', sys.argv[1:]); sys.exit(2)
if not os.isatty(1):
    print('--cloud requires an interactive terminal'); sys.exit(1)
if os.environ.get('CI'):
    print('No cloud session was created or opened: it would open in this terminal and need the keyboard. CI is set.'); sys.exit(1)
try:
    keyboard = 0 if os.isatty(0) else os.open('/dev/tty', os.O_RDONLY)
except OSError as error:
    print('No cloud session was created or opened: it would open in this terminal and need the keyboard.', error.strerror); sys.exit(1)
if os.tcgetpgrp(keyboard) != os.tcgetpgrp(1):
    print('/dev/tty is not the terminal on stdout'); sys.exit(1)
task = sys.stdin.read()
if not task.startswith('/faker-mode'):
    print('task text was', repr(task[:20])); sys.exit(2)
print('\\x1b7\\x1b[?25l\\x1b[32mStarted\\x1b[0m \\x1b]8;;x\\x07https://claude.ai/code/session_01ABC\\x1b[0m\\x1b8def', flush=True)
"""


def test_claude_runs_on_a_terminal_with_the_prompt_on_stdin_and_no_value_for_cloud(tmp_path):
    key = "0" * 32
    prompt = f"/faker-mode Work the brief. key {key}"
    script = FAKE_CLAUDE_CLOUD.replace("print('\\x1b7", f"print('task', repr(task)); print('\\x1b7")
    workdir = tmp_path / "checkouts" / "acme__shop" / "7"
    agent = fake_agent(script)
    session = cloud([]).start("claude", "acme/shop", command([*agent, "--cloud"], prompt, [key]), workdir)
    assert session == "https://claude.ai/code/session_01ABCdef"
    log = (workdir.parent / "7.log").read_text()
    assert "\x1b" not in log
    assert key not in log
    assert not list(workdir.parent.glob("*.prompt.md"))


def test_claude_launch_command_passes_a_fake_that_rejects_a_valued_cloud_flag_or_a_missing_terminal(tmp_path):
    real = launch_argv("claude", "acme/shop", 7, "small")
    assert real.argv == ("claude", "--cloud")
    wrapper = tmp_path / "bin" / "claude"
    wrapper.parent.mkdir()
    wrapper.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE_CLOUD)
    wrapper.chmod(0o755)
    workdir = tmp_path / "checkouts" / "acme__shop" / "7"
    ok = cloud([]).start("claude", "acme/shop", replace(real, argv=(str(wrapper), *real.argv[1:])), workdir)
    assert ok == "https://claude.ai/code/session_01ABCdef"
    with pytest.raises(CloudError, match="bad argv"):
        cloud([]).start("claude", "acme/shop", replace(real, argv=(str(wrapper), "--cloud", "@/tmp/p")), workdir)


def test_a_fake_claude_that_needs_a_terminal_fails_when_stdout_is_not_one(tmp_path):
    wrapper = tmp_path / "claude"
    wrapper.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE_CLOUD)
    wrapper.chmod(0o755)
    result = subprocess.run([str(wrapper), "--cloud"], input="/faker-mode x", capture_output=True, text=True)
    assert result.returncode == 1 and "interactive terminal" in result.stdout


@pytest.mark.parametrize("backend", ["codex", "claude"])
def test_echoed_signing_prompt_is_redacted_from_log_and_error(tmp_path, backend):
    key = "0123456789abcdef" * 2
    private_command = verify_argv(
        backend,
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
        cloud([]).start(backend, "acme/shop", private_command, workdir)

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
        cloud([]).start("codex", "acme/shop", command(argv), tmp_path / "w")


def test_an_agent_that_prints_no_session_in_time_is_an_error(tmp_path):
    argv = fake_agent("import time; time.sleep(30)")
    with pytest.raises(CloudError, match="no session in 2s"):
        cloud([], wait=2.0).start("codex", "acme/shop", command(argv), tmp_path / "w")


def test_the_pseudo_terminal_is_the_childs_controlling_terminal_so_dev_tty_opens(tmp_path):
    script = ("import os; fd = os.open('/dev/tty', os.O_RDONLY); "
              "print('tty', os.tcgetpgrp(fd) == os.tcgetpgrp(1), 'stdin_tty', os.isatty(0), 'sid', os.getsid(0) == os.getpid()); "
              "print('Session: https://claude.ai/code/session_01ABCdef', flush=True)")
    workdir = tmp_path / "w"
    assert cloud([]).start("claude", "acme/shop", command(fake_agent(script)), workdir) == "https://claude.ai/code/session_01ABCdef"
    log = (tmp_path / "w.log").read_text()
    assert "tty True stdin_tty False" in log


def test_the_fake_claude_fails_on_a_pseudo_terminal_that_is_not_the_controlling_terminal(tmp_path):
    # The old launch: new session, pty on stdout, never acquired. /dev/tty then fails with ENXIO.
    import os

    wrapper = tmp_path / "claude"
    wrapper.write_text(f"#!{sys.executable}\n" + FAKE_CLAUDE_CLOUD)
    wrapper.chmod(0o755)
    master, slave = os.openpty()
    try:
        proc = subprocess.Popen([str(wrapper), "--cloud"], stdin=subprocess.PIPE, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        proc.stdin.write(b"/faker-mode x")
        proc.stdin.close()
        # Read while the child runs: macOS drops unread pty output once the last slave descriptor closes.
        out = b""
        while True:
            try:
                chunk = os.read(master, 65536)
            except OSError:  # EIO once the slave side is gone (Linux)
                break
            if not chunk:
                break
            out += chunk
        proc.wait(timeout=10)
        out = out.decode()
    finally:
        os.close(master)
    assert proc.returncode == 1 and "need the keyboard" in out


def test_ci_is_not_passed_to_the_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "true")
    script = "import os; print('ci', repr(os.environ.get('CI'))); print('task_e1b2c3d4e5', flush=True)"
    cloud([]).start("claude", "acme/shop", command(fake_agent(script)), tmp_path / "w")
    assert "ci None" in (tmp_path / "w.log").read_text()


def test_the_child_survives_until_the_session_exists_is_reaped_and_leaks_no_descriptors(tmp_path):
    import os

    fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    before = len(os.listdir(fd_dir))
    script = ("import time; time.sleep(0.5); print('Session: https://claude.ai/code/session_01ABCdef', flush=True); time.sleep(0.3)")
    procs = []

    def spy(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        procs.append(proc)
        return proc

    runner = cloud([])
    runner.popen = spy
    assert runner.start("claude", "acme/shop", command(fake_agent(script)), tmp_path / "w") == "https://claude.ai/code/session_01ABCdef"
    deadline = time.time() + 10
    while procs[0].returncode is None and time.time() < deadline:
        time.sleep(0.05)
    assert procs[0].returncode == 0, "the drain thread reaps the child after it exits"
    deadline = time.time() + 5
    while len(os.listdir(fd_dir)) > before and time.time() < deadline:
        time.sleep(0.05)
    assert len(os.listdir(fd_dir)) <= before
