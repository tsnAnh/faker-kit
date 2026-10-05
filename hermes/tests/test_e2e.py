import importlib.util
import json
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


@pytest.fixture
def e2e():
    return load_e2e()


def test_argument_validation_requires_an_explicit_matching_scratch_repo(e2e):
    with pytest.raises(SystemExit):
        e2e.parse_args(["--repo", "acme/scratch"])
    with pytest.raises(SystemExit):
        e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/prod"])
    config = e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch", "--dry-run"])
    assert config.repo == "acme/scratch" and config.dry_run is True


def test_codex_backend_requires_an_environment(e2e):
    with pytest.raises(SystemExit):
        e2e.parse_args(["--repo", "acme/scratch", "--confirm-scratch-repo", "acme/scratch", "--backend", "codex"])


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
