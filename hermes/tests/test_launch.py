import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import replace
from hashlib import sha256

import pytest
from conftest import HERMES, KIT
from team_kit_cloud import Cloud, CloudError, owned_routine, post_json
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
        ("claude", "large", (), CLAUDE),
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

    return Cloud(run_status=run_status)


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


def cloud(wait=200.0):
    clock = Clock()
    return Cloud(wait_seconds=wait, sleep=clock.sleep, clock=clock)


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
    import os

    fd_dir = "/proc/self/fd" if os.path.isdir("/proc/self/fd") else "/dev/fd"
    before = len(os.listdir(fd_dir))
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
    while len(os.listdir(fd_dir)) > before and time.time() < deadline:
        time.sleep(0.05)
    assert len(os.listdir(fd_dir)) <= before


TOKEN = "sk-ant-oat01-routine-token"
SESSION_URL = "https://claude.ai/code/cse_01XYZabc"


class RoutineApi:
    """Answers a fire as the routines API does: a status and a body. Records every request it gets."""

    def __init__(self, status=200, body=None, error=None):
        self.status, self.error, self.requests = status, error, []
        self.body = body if body is not None else {"type": "routine_fire", "claude_code_session_id": "cse_01XYZabc",
                                                    "claude_code_session_url": SESSION_URL}

    def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, json.loads(body), timeout))
        if self.error:
            raise self.error
        return self.status, self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()


def routine_cloud(api, routine_id="trig_01ABCDEFGH", repo="acme/shop", token=TOKEN):
    return Cloud(routines=owned_routine(routine_id, repo, token), post=api)


def test_a_claude_session_is_one_routine_fire_with_the_prompt_only_in_the_body(tmp_path):
    key = "0123456789abcdef" * 2
    private = verify_argv("claude", "acme/shop", 7, "https://github.com/acme/shop/pull/7", "a" * 40, key)
    api = RoutineApi()
    assert routine_cloud(api).start("claude", "acme/shop", private, tmp_path / "w") == SESSION_URL
    [(url, headers, body, timeout)] = api.requests
    assert url == "https://api.anthropic.com/v1/claude_code/routines/trig_01ABCDEFGH/fire"
    assert headers == {"Authorization": f"Bearer {TOKEN}", "anthropic-beta": "experimental-cc-routine-2026-04-01",
                       "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
    assert body == {"text": private.prompt} and key in private.prompt and key not in url
    assert 0 < timeout <= 60
    assert private.argv == () and not (tmp_path / "w").exists(), "nothing runs or clones locally"


def test_the_routine_fires_for_its_own_repository_in_any_letter_case():
    api = RoutineApi()
    assert routine_cloud(api, repo="Acme/Shop").start("claude", "acme/shop", CloudCommand((), "p"), KIT) == SESSION_URL


@pytest.mark.parametrize(
    ("api", "why"),
    [
        (RoutineApi(401, {"error": {"type": "authentication_error", "message": f"bad token {TOKEN}"}}),
         "HTTP 401; the routine token is wrong or was revoked"),
        (RoutineApi(400, {"error": {"reason": "routine_paused", "message": "Routine is paused."}}),
         "HTTP 400 routine_paused; the routine is paused"),
        (RoutineApi(429, {"error": {"reason": "rate_limited"}}), "HTTP 429 rate_limited; the routine's fire limit is reached"),
        (RoutineApi(400, {"error": {"reason": "Not A Code; secret prompt"}}), "refused the fire: HTTP 400\n"),
        (RoutineApi(502, b"<html>bad gateway</html>"), "refused the fire: HTTP 502\n"),
        (RoutineApi(200, {"type": "routine_fire"}), "answered the fire without a session URL"),
        (RoutineApi(200, {"claude_code_session_url": "https://evil.example/code/x"}), "answered the fire without a session URL"),
        (RoutineApi(error=urllib.error.URLError(f"proxy said {TOKEN}")), "did not reach the API (URLError)"),
        (RoutineApi(error=TimeoutError()), "did not reach the API (TimeoutError)"),
    ],
)
def test_a_refused_or_failed_fire_is_one_attempt_and_an_error_without_the_token_or_prompt(api, why):
    with pytest.raises(CloudError) as raised:
        routine_cloud(api).start("claude", "acme/shop", CloudCommand((), "secret prompt with key 00ff"), KIT)
    error = str(raised.value) + "\n"
    assert why in error
    assert TOKEN not in error and "secret prompt" not in error
    assert len(api.requests) == 1, "a fire is never retried: one that timed out may have started a session"


@pytest.mark.parametrize(
    ("routine_id", "owner", "token", "why"),
    [
        ("trig_01ABCDEFGH", "acme/other", TOKEN, "acme/shop has no Claude Code routine"),
        ("trig_01ABCDEFGH", "acme/shop", "", "no routine token"),
        ("trig_../../x", "acme/shop", TOKEN, "routine id must look like"),
    ],
)
def test_no_fire_without_this_repositorys_routine_and_token(routine_id, owner, token, why):
    api = RoutineApi()
    with pytest.raises(CloudError, match=why):
        routine_cloud(api, routine_id, owner, token).start("claude", "acme/shop", CloudCommand((), "p"), KIT)
    assert api.requests == []


def test_post_json_never_follows_a_redirect_that_would_carry_the_token(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self._answer()

        def do_GET(self):
            self._answer()

        def _answer(self):
            hits.append((self.command, self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, _ = post_json(f"http://127.0.0.1:{server.server_port}/fire", {"Authorization": "Bearer t"}, b"{}", 5)
    finally:
        server.shutdown()
        server.server_close()
    assert status == 302
    assert hits == [("POST", "/fire", "Bearer t")]
