import re
import subprocess
import sys
import time
from hashlib import sha256

import pytest
from conftest import HERMES, KIT
from team_kit_cloud import Cloud, CloudError
from team_kit_contracts import CloudCommand, LAUNCH_PROMPTS, PROMPT_FILE, PROMPT_STDIN, ContractError, cloud_argv, launch_argv

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
    ("backend", "size", "expected_argv", "transport", "prompt"),
    [
        ("claude", "large", ("claude", "--cloud"), PROMPT_FILE, CLAUDE),
        ("codex", "small", ("codex", "cloud", "exec", "--env", "env_7", "--attempts", "1", "-"), PROMPT_STDIN, CODEX),
        ("codex", "medium", ("codex", "cloud", "exec", "--env", "env_7", "--attempts", "2", "-"), PROMPT_STDIN, CODEX),
        ("codex", "large", ("codex", "cloud", "exec", "--env", "env_7", "--attempts", "3", "-"), PROMPT_STDIN, CODEX),
    ],
)
def test_launch_argv_is_exact_and_keeps_prompt_out_of_argv(backend, size, expected_argv, transport, prompt):
    command = launch_argv(backend, "acme/shop", 123, size, "env_7")
    assert command.argv == expected_argv
    assert command.prompt_transport == transport
    assert command.prompt == prompt
    assert command.prompt.startswith(PREFIXES)
    assert command.prompt not in command.argv


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


def command(argv, prompt="prompt", transport=PROMPT_STDIN):
    return CloudCommand(tuple(argv), prompt, transport)


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


def test_claude_prompt_file_is_private_and_argv_carries_only_the_file_reference(tmp_path):
    key = "0" * 32
    prompt = f"/verify-unit Sign the verdict with key {key}."
    script = ("import hashlib, pathlib, sys; p = pathlib.Path(sys.argv[1][1:]); data = p.read_text(); "
              "print('argv', sys.argv[1:]); print('mode', oct(p.stat().st_mode & 0o777)); "
              "print('digest', hashlib.sha256(data.encode()).hexdigest()); "
              "print('Session: https://claude.ai/code/session_01ABCdef', flush=True)")
    workdir = tmp_path / "checkouts" / "acme__shop" / "7"
    assert cloud([]).start("claude", "acme/shop", command(fake_agent(script), prompt, PROMPT_FILE), workdir) == "https://claude.ai/code/session_01ABCdef"
    log = (workdir.parent / "7.log").read_text()
    assert "argv ['@" in log
    assert "mode 0o600" in log
    assert f"digest {sha256(prompt.encode()).hexdigest()}" in log
    assert key not in log
    assert not (workdir.parent / "7.prompt.md").exists()


def test_an_agent_that_exits_without_a_session_is_an_error(tmp_path):
    argv = fake_agent("import sys; print('not signed in'); sys.exit(3)")
    with pytest.raises(CloudError, match="exited 3 before starting a session: not signed in"):
        cloud([]).start("codex", "acme/shop", command(argv), tmp_path / "w")


def test_an_agent_that_prints_no_session_in_time_is_an_error(tmp_path):
    argv = fake_agent("import time; time.sleep(30)")
    with pytest.raises(CloudError, match="no session in 2s"):
        cloud([], wait=2.0).start("codex", "acme/shop", command(argv), tmp_path / "w")
