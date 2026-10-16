"""claude-review must fail unless claude[bot] submitted a terminal review on the exact head."""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "claude_review_postcondition.py"
HEAD = "d77ef8881127f63f1c0fc940106aeefb866796ea"


def run(tmp_path, reviews, execution):
    rp, ep = tmp_path / "r.json", tmp_path / "e.json"
    rp.write_text(json.dumps(reviews))
    if execution is not None:
        ep.write_text(json.dumps(execution))
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), str(rp), str(ep), HEAD], capture_output=True, text=True, check=False)
    return p.returncode, p.stdout


def review(user, state, commit):
    return {"user": {"login": user}, "state": state, "commit_id": commit}


def result(denials):
    return [{"type": "system"}, {"type": "result", "permission_denials": denials}]


def test_approve_on_head_passes(tmp_path):
    rc, out = run(tmp_path, [review("claude[bot]", "APPROVED", HEAD)], result([]))
    assert rc == 0 and "APPROVED" in out


def test_changes_requested_on_head_passes(tmp_path):
    rc, _ = run(tmp_path, [review("claude[bot]", "CHANGES_REQUESTED", HEAD)], result([]))
    assert rc == 0


def test_green_action_without_a_review_fails_and_counts_denials(tmp_path):
    denial = {"tool_name": "Bash", "tool_input": {"command": "gh api x -f event=APPROVE"}}
    rc, out = run(tmp_path, [], result([denial, denial]))
    assert rc == 1 and "denials: 2" in out and "denied: Bash" in out


def test_review_on_an_older_head_fails(tmp_path):
    rc, out = run(tmp_path, [review("claude[bot]", "APPROVED", "0" * 40)], result([]))
    assert rc == 1 and "on this head: 0" in out


def test_other_reviewers_do_not_count(tmp_path):
    reviews = [review("chatgpt-codex-connector[bot]", "COMMENTED", HEAD), review("tsnAnh", "APPROVED", HEAD)]
    rc, out = run(tmp_path, reviews, result([]))
    assert rc == 1 and "reviews on any commit: 0" in out


def test_a_comment_only_review_is_not_terminal(tmp_path):
    rc, _ = run(tmp_path, [review("claude[bot]", "COMMENTED", HEAD)], result([]))
    assert rc == 1


def test_missing_execution_log_still_fails_closed(tmp_path):
    rc, out = run(tmp_path, [], None)
    assert rc == 1 and "no readable execution log" in out


def test_a_denied_command_is_one_line_without_workflow_commands(tmp_path):
    denial = {"tool_name": "Bash", "tool_input": {"command": "echo ::set-output name=x::y\nsecret"}}
    rc, out = run(tmp_path, [], result([denial]))
    assert rc == 1 and "::" not in out.split("denied:")[1] and "\n  Fix" in out
