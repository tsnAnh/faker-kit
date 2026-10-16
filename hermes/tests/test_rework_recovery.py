import fcntl
import json

import pytest
from team_kit_contracts import REWORK_PROMPTS
from team_kit_gh import Refused
from test_gh import HEAD, NOW, REPO, FakeCloud, FakeGh, run, tick
from test_rework import NEW, finding, reviewed, rework, stale_report, stdout

REVIEW = {"agent-task", "agent:review"}
RUNNING = {"agent-task", "agent:running"}


@pytest.fixture
def gh(kit):
    return FakeGh(kit)


def record(team_kit_home, n):
    path = team_kit_home / "rework" / "meo-beo" / f"{n}.json"
    return json.loads(path.read_text()) if path.exists() else None


def failing_edits(gh, fail):
    """gh with `issue edit` calls failing at the given 1-based positions."""
    real, edits = gh.__call__, []

    def flaky(args, input=None):
        if args[:2] == ["issue", "edit"]:
            edits.append(args)
            if len(edits) in fail:
                raise Refused("gh issue failed: 502")
        return real(args, input)

    return flaky


class Crash(BaseException):
    pass


class CrashingCloud(FakeCloud):
    def start(self, backend, repo, argv, workdir):
        self.started.append((backend, repo, argv, workdir))
        raise Crash


def recoveries(gh, n):
    return [c for c in gh.comments[(REPO, n)] if c["body"].startswith("<!-- team-kit:rework-recovered v1 -->")]


def in_flight(team_kit_home):
    return json.loads((team_kit_home / "in-flight" / "meo-beo.json").read_text())["repos"]


# F5: only an authorized reviewer's findings, or a person's changes-requested review, on this exact head send a unit back.


@pytest.mark.parametrize("login", ["dependabot[bot]", "github-actions[bot]"])
def test_an_unrelated_bots_review_on_the_head_is_not_a_finding(team_kit_home, gh, capsys, login):
    n = reviewed(gh, with_finding=False)
    gh.reviews[7] = [{"user": {"login": login, "type": "Bot"}, "state": "COMMENTED", "commit_id": HEAD, "body": "looks good, no findings"}]
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) != 0 and not cloud.started
    assert "nothing to rework" in capsys.readouterr().err and gh.labels(REPO, n) == REVIEW


@pytest.mark.parametrize("state", ["CHANGES_REQUESTED", "COMMENTED"])
def test_an_unrelated_bots_inline_comment_or_changes_request_is_not_a_finding(team_kit_home, gh, capsys, state):
    n = reviewed(gh, with_finding=False)
    gh.pr_comments_inline[7] = [{"user": {"login": "dependabot[bot]", "type": "Bot"}, "commit_id": HEAD, "body": "bump"}]
    gh.reviews[7] = [{"user": {"login": "dependabot[bot]", "type": "Bot"}, "state": state, "commit_id": HEAD}]
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) != 0 and not cloud.started


@pytest.mark.parametrize("login", ["claude[bot]", "chatgpt-codex-connector[bot]", "alice"])
def test_an_approval_is_never_a_finding_whoever_wrote_it(team_kit_home, kit, capsys, login):
    gh = FakeGh(kit, review_app="claude[bot]", verifier_backend="none")
    n = reviewed(gh, with_finding=False)
    gh.reviews[7] = [{"user": {"login": login, "type": "User" if login == "alice" else "Bot"}, "state": "APPROVED", "commit_id": HEAD, "body": "P1"}]
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) != 0 and not cloud.started


@pytest.mark.parametrize("login", ["claude[bot]", "chatgpt-codex-connector[bot]"])
def test_an_authorized_apps_praise_without_an_inline_finding_is_not_a_finding(team_kit_home, kit, capsys, login):
    gh = FakeGh(kit, review_app="claude[bot]", verifier_backend="none")
    n = reviewed(gh, with_finding=False)
    gh.reviews[7] = [{"user": {"login": login, "type": "Bot"}, "state": "COMMENTED", "commit_id": HEAD, "body": "Looks good, no findings"}]
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) != 0 and not cloud.started


@pytest.mark.parametrize(
    ("login", "review_app", "review", "inline"),
    [
        ("claude[bot]", "claude[bot]", "CHANGES_REQUESTED", False),
        ("claude[bot]", "claude[bot]", None, True),
        ("chatgpt-codex-connector[bot]", None, None, True),
        ("chatgpt-codex-connector[bot]", "claude[bot]", "CHANGES_REQUESTED", False),
        ("alice", None, "CHANGES_REQUESTED", False),
    ],
    ids=["review-app-changes", "review-app-inline", "codex-inline", "codex-changes", "human-changes"],
)
def test_an_authorized_reviewers_finding_or_a_persons_changes_request_on_the_head_is_evidence(team_kit_home, kit, capsys, login, review_app, review, inline):
    gh = FakeGh(kit, **({"review_app": review_app, "verifier_backend": "none"} if review_app else {}))
    n = reviewed(gh, with_finding=False)
    user = {"login": login, "type": "User" if login == "alice" else "Bot"}
    gh.reviews[7] = [{"user": user, "state": review, "commit_id": HEAD}] if review else []
    gh.pr_comments_inline[7] = [{"user": user, "commit_id": HEAD, "original_commit_id": HEAD, "body": "P1"}] if inline else []
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1


def test_an_authorized_finding_first_made_on_an_older_head_is_not_evidence_for_this_one(team_kit_home, gh, capsys):
    n = reviewed(gh, with_finding=False)
    old = "c" * 40
    gh.reviews[7] = [{"user": {"login": "alice", "type": "User"}, "state": "CHANGES_REQUESTED", "commit_id": old}]
    gh.pr_comments_inline[7] = [{"user": {"login": "chatgpt-codex-connector[bot]", "type": "Bot"}, "commit_id": HEAD, "original_commit_id": old, "body": "P1"}]
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) != 0 and not cloud.started


# F3 and F2: a rework that started no session never leaves the unit running, holding a slot, or barred from a retry.


def test_a_failed_start_whose_return_relabel_also_fails_once_still_returns_the_unit_to_review(team_kit_home, gh, capsys):
    n = reviewed(gh)
    flaky = failing_edits(gh, {2})
    assert rework(flaky, n, cloud=FakeCloud(fail="no session")) == 1
    assert "launch failed" in capsys.readouterr().err
    assert gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None


def test_a_failed_start_whose_return_relabel_keeps_failing_is_recovered_by_the_next_tick(team_kit_home, gh, capsys):
    n = reviewed(gh)
    flaky = failing_edits(gh, set(range(2, 10)))
    assert rework(flaky, n, cloud=FakeCloud(fail="no session")) == 1
    capsys.readouterr()
    assert gh.labels(REPO, n) == RUNNING and record(team_kit_home, n)["state"] == "failed", "the transition is kept, not lost"
    notices = tick(gh, capsys)
    assert any(f"#{n}" in line and "back in agent:review" in line for line in notices)
    assert gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None
    [audit] = recoveries(gh, n)
    assert f"head: {HEAD}" in audit["body"] and "state: failed" in audit["body"] and "rework-of" not in audit["body"]
    assert in_flight(team_kit_home) == {REPO: 0}
    assert tick(gh, capsys) == ["[SILENT]"] and len(recoveries(gh, n)) == 1, "reconcile is idempotent"
    good = FakeCloud()
    assert rework(gh, n, cloud=good) == 0 and len(good.started) == 1


def test_a_failed_start_whose_return_relabel_keeps_failing_is_recovered_by_the_next_rework(team_kit_home, gh, capsys):
    n = reviewed(gh)
    assert rework(failing_edits(gh, set(range(2, 10))), n, cloud=FakeCloud(fail="no session")) == 1
    capsys.readouterr()
    good = FakeCloud()
    assert rework(gh, n, cloud=good) == 0 and len(good.started) == 1
    assert len(recoveries(gh, n)) == 1 and gh.labels(REPO, n) == RUNNING and record(team_kit_home, n)["state"] == "started"
    assert in_flight(team_kit_home) == {REPO: 1}


def test_a_relabel_to_running_that_fails_after_github_applied_it_is_undone(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__
    edits = []

    def applied_then_failed(args, input=None):
        if args[:2] == ["issue", "edit"]:
            edits.append(args)
            real(args, input)
            if len(edits) == 1:
                raise Refused("gh issue failed: 502")
            return ""
        return real(args, input)

    cloud = FakeCloud()
    assert rework(applied_then_failed, n, cloud=cloud) == 1
    assert cloud.started == [] and gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None


def test_a_relabel_to_running_that_github_never_applied_leaves_the_unit_in_review_and_no_record(team_kit_home, gh, capsys):
    n = reviewed(gh)
    cloud = FakeCloud()
    assert rework(failing_edits(gh, set(range(1, 10))), n, cloud=cloud) == 1
    assert cloud.started == [] and gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None
    capsys.readouterr()
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1 and recoveries(gh, n) == []


def test_a_relabel_to_running_whose_undo_cannot_read_the_issue_keeps_a_record_that_does_not_bar_a_retry(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real, state = gh.__call__, {"edit": False}

    def down_after_edit(args, input=None):
        if args[:2] == ["issue", "edit"] and not state["edit"]:
            state["edit"] = True
            raise Refused("gh issue failed: 502")
        if state["edit"] and args[0] == "api" and args[-1] == f"repos/{REPO}/issues/{n}":
            raise Refused("gh api failed: 502")
        return real(args, input)

    cloud = FakeCloud()
    assert rework(down_after_edit, n, cloud=cloud) == 1
    assert cloud.started == [] and record(team_kit_home, n)["state"] == "failed"
    capsys.readouterr()
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1 and len(recoveries(gh, n)) == 1


def test_a_crash_inside_the_start_leaves_a_record_that_a_retry_on_the_same_head_reconciles(team_kit_home, gh, capsys):
    n = reviewed(gh)
    with pytest.raises(Crash):
        rework(gh, n, cloud=CrashingCloud())
    assert gh.labels(REPO, n) == RUNNING and record(team_kit_home, n)["state"] == "failed"
    gh.labels(REPO, n).clear()
    gh.labels(REPO, n).update(REVIEW)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1
    assert record(team_kit_home, n)["state"] == "started" and len(recoveries(gh, n)) == 1


def test_a_crash_inside_the_start_without_label_repair_is_reconciled_by_a_retry(team_kit_home, gh, capsys):
    n = reviewed(gh)
    with pytest.raises(Crash):
        rework(gh, n, cloud=CrashingCloud())
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1 and gh.labels(REPO, n) == RUNNING


def test_a_crash_before_the_start_is_reconciled_by_tick(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__

    def crash_after_relabel(args, input=None):
        out = real(args, input)
        if args[:2] == ["issue", "edit"]:
            raise Crash
        return out

    cloud = FakeCloud()
    with pytest.raises(Crash):
        rework(crash_after_relabel, n, cloud=cloud)
    assert cloud.started == [] and gh.labels(REPO, n) == RUNNING and record(team_kit_home, n)["state"] == "starting"
    tick(gh, capsys)
    assert gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None and len(recoveries(gh, n)) == 1
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1


def test_tick_leaves_a_starting_rework_alone_while_its_process_still_holds_the_lock(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__

    def crash_after_relabel(args, input=None):
        out = real(args, input)
        if args[:2] == ["issue", "edit"]:
            raise Crash
        return out

    with pytest.raises(Crash):
        rework(crash_after_relabel, n, cloud=FakeCloud())
    with (team_kit_home / "rework" / "meo-beo" / f"{n}.lock").open("w") as live:
        fcntl.flock(live, fcntl.LOCK_EX)
        tick(gh, capsys)
        assert gh.labels(REPO, n) == RUNNING and record(team_kit_home, n)["state"] == "starting" and recoveries(gh, n) == []
    tick(gh, capsys)
    assert gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None

def test_a_record_left_by_a_crash_does_not_hide_the_done_report_from_tick_or_plain_launch(team_kit_home, gh, capsys):
    n = reviewed(gh)
    with pytest.raises(Crash):
        rework(gh, n, cloud=CrashingCloud())
    capsys.readouterr()
    assert run(gh, "launch", "--issue", str(n)) == 1
    assert "waits for the owner to merge" in capsys.readouterr().err, "launch reconciled it back to review first"
    assert gh.labels(REPO, n) == REVIEW and record(team_kit_home, n) is None


def test_a_rework_whose_session_started_still_refuses_a_duplicate_after_its_labels_were_reset(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__

    def no_comment(args, input=None):
        if args[:2] == ["issue", "comment"]:
            raise RuntimeError("rate limited")
        return real(args, input)

    assert rework(no_comment, n, cloud=FakeCloud()) == 0
    assert record(team_kit_home, n)["state"] == "started" and record(team_kit_home, n)["comment"] is False
    capsys.readouterr()
    gh.labels(REPO, n).clear()
    gh.labels(REPO, n).update(REVIEW)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 1 and "already requested" in capsys.readouterr().err and cloud.started == []
    assert tick(gh, capsys) == ["[SILENT]"] and recoveries(gh, n) == []


# F4 and F7 stay fail-closed.


def test_a_failed_audit_comment_bars_old_reports_a_new_head_report_ends_it_and_only_a_new_head_reworks_again(team_kit_home, kit, capsys):
    gh = FakeGh(kit, verifier_backend="none")
    n = reviewed(gh)
    real = gh.__call__

    def no_comment(args, input=None):
        if args[:2] == ["issue", "comment"]:
            raise RuntimeError("rate limited")
        return real(args, input)

    assert rework(no_comment, n, cloud=FakeCloud()) == 0
    assert "audit" in stdout(capsys)
    stale_report(gh, n)
    assert tick(gh, capsys) == ["[SILENT]"] and gh.labels(REPO, n) == RUNNING
    gh.prs[(REPO, 7)]["headRefOid"] = NEW
    stale_report(gh, n, NEW)
    tick(gh, capsys)
    assert gh.labels(REPO, n) == REVIEW
    cloud = FakeCloud()
    assert rework(gh, n, head=HEAD, cloud=cloud) == 1 and cloud.started == []
    capsys.readouterr()
    finding(gh, head=NEW)
    assert rework(gh, n, head=NEW, cloud=cloud) == 0 and len(cloud.started) == 1


def test_the_same_head_is_refused_after_a_rework_whose_audit_comment_failed(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__

    def no_comment(args, input=None):
        if args[:2] == ["issue", "comment"]:
            raise RuntimeError("rate limited")
        return real(args, input)

    assert rework(no_comment, n, cloud=FakeCloud()) == 0
    gh.labels(REPO, n).clear()
    gh.labels(REPO, n).update(REVIEW)
    capsys.readouterr()
    assert rework(gh, n, cloud=FakeCloud()) == 1 and "already requested" in capsys.readouterr().err


@pytest.mark.parametrize("head", ["none", "zzzz", "12"])
def test_a_report_with_a_missing_or_malformed_head_never_ends_a_rework(team_kit_home, gh, capsys, head):
    n = reviewed(gh)
    assert rework(gh, n, cloud=FakeCloud()) == 0
    gh.prs[(REPO, 7)]["headRefOid"] = NEW
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: done\nbranch: dispatch/x\nhead: {head}\npr: https://github.com/{REPO}/pull/7\n", login="Codertocat", at=NOW)
    capsys.readouterr()
    assert tick(gh, capsys) == ["[SILENT]"] and gh.labels(REPO, n) == RUNNING


# F1: both rework prompts ask for a report of the new head.


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_every_rework_prompt_asks_for_a_new_report_of_the_new_head_and_keeps_its_fallback(backend):
    prompt = REWORK_PROMPTS[backend]
    assert "post a new report whose head is the new commit" in prompt
    assert "If the follow-brief skill is not available in this session" in prompt

