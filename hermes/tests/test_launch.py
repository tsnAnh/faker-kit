import re
import subprocess
import sys
import time

import pytest
from conftest import HERMES, KIT
from team_kit_cloud import Cloud, CloudError, codex_cloud_result_capability
from team_kit_contracts import LAUNCH_PROMPTS, ContractError, cloud_argv, launch_argv

PREFIXES = ("/faker-mode ", "$faker-mode ")


@pytest.mark.parametrize("backend", sorted(LAUNCH_PROMPTS))
def test_every_launch_prompt_template_starts_with_faker_mode(backend):
    assert LAUNCH_PROMPTS[backend].startswith(PREFIXES)


CLAUDE = ("/faker-mode Work the brief in acme/shop#123 using the follow-brief skill. If the follow-brief skill is not available in this session, "
          "comment on the issue with `<!-- team-kit:report v1 -->` as the first line and `status: blocked` as the second, "
          "say the team-kit skills did not load, and stop.")
CODEX = ("$faker-mode Work the brief in acme/shop#123 using the follow-brief skill. "
         "If the follow-brief skill is not available in this session, stop and say the team-kit skills did not load.")

CODEX_CLOUD_HELP = """[EXPERIMENTAL] Browse tasks from Codex Cloud and apply changes locally

Usage: codex cloud [OPTIONS] [COMMAND]

Commands:
  exec    Submit a new Codex Cloud task without launching the TUI
  status  Show the status of a Codex Cloud task
  list    List Codex Cloud tasks
  apply   Apply the diff for a Codex Cloud task locally
  diff    Show the unified diff for a Codex Cloud task
  help    Print this message or the help of the given subcommand(s)
"""

CODEX_CLOUD_LIST_HELP = """List Codex Cloud tasks

Usage: codex cloud list [OPTIONS]

Options:
      --limit <N>
      --json
          Emit JSON instead of plain text
"""

CODEX_CLOUD_STATUS_HELP = """Show the status of a Codex Cloud task

Usage: codex cloud status [OPTIONS] <TASK_ID>

Arguments:
  <TASK_ID>
          Codex Cloud task identifier to inspect
"""


@pytest.mark.parametrize(
    ("backend", "size", "expected"),
    [
        ("claude", "large", ["claude", "--cloud", CLAUDE]),
        ("codex", "small", ["codex", "cloud", "exec", "--env", "env_7", "--attempts", "1", CODEX]),
        ("codex", "medium", ["codex", "cloud", "exec", "--env", "env_7", "--attempts", "2", CODEX]),
        ("codex", "large", ["codex", "cloud", "exec", "--env", "env_7", "--attempts", "3", CODEX]),
    ],
)
def test_launch_argv_is_exact_and_needs_no_shell(backend, size, expected):
    argv = launch_argv(backend, "acme/shop", 123, size, "env_7")
    assert argv == expected
    assert argv[-1].startswith(PREFIXES)


def test_codex_cloud_result_capability_documents_the_installed_cli_surface():
    found = codex_cloud_result_capability("codex-cli 0.158.0", CODEX_CLOUD_HELP, CODEX_CLOUD_LIST_HELP, CODEX_CLOUD_STATUS_HELP)
    assert found.commands == ("exec", "status", "list", "apply", "diff", "help")
    assert found.list_json is True
    assert found.status_json is False
    assert found.final_message_command is None
    assert found.can_read_final_message is False
    assert found.summary() == ("codex-cli 0.158.0: codex cloud commands: exec, status, list, apply, diff, help; "
                               "no final-message subcommand; status has no --json; list --json lists tasks only")


def test_codex_cloud_result_capability_would_notice_a_supported_final_message_command():
    help_text = CODEX_CLOUD_HELP.replace("  help    Print this message", "  final-message  Show the final message for a finished task\n  help    Print this message")
    found = codex_cloud_result_capability("codex-cli future", help_text, CODEX_CLOUD_LIST_HELP, CODEX_CLOUD_STATUS_HELP)
    assert found.can_read_final_message is True
    assert found.final_message_command == "final-message"


def test_a_claude_session_without_the_kit_posts_a_report_tick_can_read():
    from team_kit_contracts import parse_report

    prompt = launch_argv("claude", "acme/shop", 1, "small")[-1]
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
    """Every `claude --cloud` or `codex cloud exec` prompt written in the kit's dispatch prose."""
    files = [*HERMES.rglob("*.md"), *(KIT / "plugins" / "trust-ladder" / "skills").glob("*dispatch*/**/*.md"),
             *(KIT / "plugins" / "trust-ladder" / "skills" / "follow-brief").rglob("*.md"),
             KIT / "docs" / "how-to" / "dispatch-with-hermes.md", KIT / "plugins" / "trust-ladder" / "references" / "compat.md"]
    pattern = re.compile(r"(?:claude --cloud|codex cloud exec(?: --env \S+)?(?: --attempts \S+)?) (['\"])(.*?)\1")
    return [(f.relative_to(KIT), m.group(2)) for f in files for m in pattern.finditer(f.read_text())]


def test_every_written_launch_prompt_starts_with_faker_mode():
    prompts = written_launch_prompts()
    assert len(prompts) >= 2
    assert [(str(f), p) for f, p in prompts if not p.startswith(PREFIXES)] == []


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
    assert cloud(calls).start("claude", "acme/shop", argv, workdir) == "https://claude.ai/code/session_01ABCdef"
    assert calls == [["repo", "clone", "acme/shop", str(workdir), "--", "--depth", "1", "--quiet"]]
    assert not (workdir / "stale").exists()
    assert f"cwd {workdir}" in (workdir.parent / "7.log").read_text()


def test_codex_starts_in_an_empty_directory_without_cloning(tmp_path):
    calls = []
    argv = fake_agent("print('task_e1b2c3d4e5')")
    assert cloud(calls).start("codex", "acme/shop", argv, tmp_path / "w") == "task_e1b2c3d4e5"
    assert calls == []


def test_an_agent_that_exits_without_a_session_is_an_error(tmp_path):
    argv = fake_agent("import sys; print('not signed in'); sys.exit(3)")
    with pytest.raises(CloudError, match="exited 3 before starting a session: not signed in"):
        cloud([]).start("codex", "acme/shop", argv, tmp_path / "w")


def test_an_agent_that_prints_no_session_in_time_is_an_error(tmp_path):
    argv = fake_agent("import time; time.sleep(30)")
    with pytest.raises(CloudError, match="no session in 2s"):
        cloud([], wait=2.0).start("codex", "acme/shop", argv, tmp_path / "w")
