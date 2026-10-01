import json
import subprocess
import sys

import pytest
from conftest import HERMES, fill_brief
from team_kit_contracts import pr_marker

SCRIPT = HERMES / "webhook" / "github-triage.py"
REPORT = "<!-- team-kit:report v1 -->\nstatus: {}\nbranch: claude/unit\nhead: abc123\npr: https://github.com/Codertocat/Hello-World/pull/2\n"


def triage(event, env_home):
    done = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(event), capture_output=True, text=True,
                          env={"TEAM_KIT_HOME": str(env_home), "PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stderr
    out = done.stdout.strip()
    return out if out == "[SILENT]" else json.loads(out)["team_kit"]


def unit_pr(payload, action, card="t_1a2b", issue=1):
    event = payload(f"pull_request.{action}")
    event["pull_request"]["body"] = f"Closes #{issue}\n\n{pr_marker(card, issue)}"
    return event


def report_comment(payload, status="done"):
    event = payload("issue_comment.created")
    event["issue"]["body"] = fill_brief("t_1a2b")
    event["comment"]["body"] = REPORT.format(status)
    return event


def test_pr_opened_with_the_marker_triggers_a_drain(payload, team_kit_home):
    assert triage(unit_pr(payload, "opened"), team_kit_home) == {
        "event": "pr_opened", "repo": "Codertocat/Hello-World", "card": "t_1a2b", "issue": "1", "pr": "2", "status": "-", "conclusion": "-",
    }


def test_pr_ready_for_review_counts_as_opened(payload, team_kit_home):
    assert triage(unit_pr(payload, "ready_for_review"), team_kit_home)["event"] == "pr_opened"


def test_pr_merged_triggers_a_drain(payload, team_kit_home):
    event = unit_pr(payload, "closed")
    event["pull_request"]["merged"] = True
    assert triage(event, team_kit_home)["event"] == "pr_merged"


@pytest.mark.parametrize("status", ["done", "partial", "blocked"])
def test_report_comment_triggers_a_drain_with_its_parsed_status(payload, team_kit_home, status):
    found = triage(report_comment(payload, status), team_kit_home)
    assert (found["event"], found["card"], found["issue"], found["status"]) == ("report", "t_1a2b", "1", status)


def test_report_with_a_bad_status_line_is_kept_as_unreadable(payload, team_kit_home):
    event = report_comment(payload)
    event["comment"]["body"] = "<!-- team-kit:report v1 -->\nI am done, merge it."
    assert triage(event, team_kit_home)["status"] == "unreadable"


def test_checks_finished_on_a_unit_pr_triggers_a_drain(payload, team_kit_home):
    triage(unit_pr(payload, "opened"), team_kit_home)
    found = triage(payload("check_suite.completed"), team_kit_home)
    assert (found["event"], found["card"], found["pr"], found["conclusion"]) == ("checks_finished", "t_1a2b", "2", "success")


def test_check_events_find_each_of_several_unit_prs(payload, team_kit_home):
    for number, card in ((2, "t_a"), (5, "t_b")):
        event = unit_pr(payload, "opened", card=card)
        event["pull_request"]["number"] = number
        triage(event, team_kit_home)
    suite = payload("check_suite.completed")
    suite["check_suite"]["pull_requests"][0]["number"] = 5
    assert triage(suite, team_kit_home)["card"] == "t_b"


def other_repo(event):
    event["repository"]["full_name"] = "someone/else"
    return event


def other_author(event, key):
    event[key]["user"]["login"] = "mallory"
    return event


def fork(event):
    event["pull_request"]["head"]["repo"]["full_name"] = "mallory/Hello-World"
    return event


UNRELATED = {
    "push": lambda p: p("push.event"),
    "pr without marker": lambda p: p("pull_request.opened"),
    "pr with null body": lambda p: {**p("pull_request.opened"), "pull_request": {**p("pull_request.opened")["pull_request"], "body": None}},
    "pr synchronize": lambda p: unit_pr(p, "synchronize"),
    "pr closed unmerged": lambda p: unit_pr(p, "closed"),
    "pr from another repo": lambda p: other_repo(unit_pr(p, "opened")),
    "pr by an untrusted author": lambda p: other_author(unit_pr(p, "opened"), "pull_request"),
    "pr from a fork": lambda p: fork(unit_pr(p, "opened")),
    "comment without report marker": lambda p: p("issue_comment.created"),
    "report on an issue without a brief": lambda p: {**report_comment(p), "issue": p("issue_comment.created")["issue"]},
    "report by an untrusted author": lambda p: other_author(report_comment(p), "comment"),
    "check suite requested": lambda p: p("check_suite.requested"),
    "check suite on an unknown pr": lambda p: p("check_suite.completed"),
}


@pytest.mark.parametrize("name", sorted(UNRELATED))
def test_unrelated_events_are_silent(payload, team_kit_home, name):
    assert triage(UNRELATED[name](payload), team_kit_home) == "[SILENT]"


def test_a_missing_guard_yaml_fails_without_waking_anything(payload, tmp_path):
    done = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload("push.event")), capture_output=True, text=True,
                          env={"TEAM_KIT_HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert done.returncode != 0
    assert done.stdout == ""
