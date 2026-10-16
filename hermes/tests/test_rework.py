import json

import pytest
from team_kit_contracts import LAUNCHED_MARKER
from team_kit_gh import Refused
from test_gh import BOT_LOGIN, HEAD, NOW, REPO, FakeCloud, FakeGh, gh, pr, run, tick  # noqa: F401

NEW = "b" * 40
BOT_REVIEW = {"user": {"login": "chatgpt-codex-connector[bot]", "type": "Bot"}, "state": "COMMENTED", "commit_id": HEAD, "body": "P1 finding"}


def finding(gh, number=7, head=HEAD, **extra):
    gh.reviews.setdefault(number, []).append({**BOT_REVIEW, "commit_id": head, **extra})


def reviewed(gh, labels=("agent-task", "agent:review"), head=HEAD, with_finding=True, **issue):
    n = gh.add_issue(REPO, labels=labels, **issue)
    url = pr(gh, 7, issue=n, head=head, branch="dispatch/x")
    gh.comment(REPO, n, f"{LAUNCHED_MARKER}\nsession: first\nbackend: claude\nbranch: dispatch/x\n", at=NOW - 900)
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: done\nbranch: dispatch/x\nhead: {head[:7]}\npr: {url}\n", login="Codertocat", at=NOW - 600)
    if with_finding:
        finding(gh, head=head)
    return n


def rework(gh, n, head=HEAD, pr_number="7", cloud=None, reason="Codex P1 on", *more):
    return run(gh, "rework", "--issue", str(n), "--pr", pr_number, "--head", head, "--reason", reason, *more, cloud=cloud)


def stdout(capsys):
    return json.loads(capsys.readouterr().out)


def test_one_command_sends_a_reviewed_unit_back_to_its_own_branch_and_pull_request(team_kit_home, gh, capsys):
    n = reviewed(gh)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud, reason="the review found a P1") == 0
    result = stdout(capsys)
    assert result["issue"] == n and result["pr"] == f"https://github.com/{REPO}/pull/7" and result["branch"] == "dispatch/x"
    assert result["rework_of"] == HEAD and result["state"] == "running"
    assert gh.labels(REPO, n) == {"agent-task", "agent:running"}
    [(backend, _, command, workdir)] = cloud.started
    assert backend == "claude" and command.argv == ("claude", "--cloud")
    assert f"Rework the brief in {REPO}#{n}" in command.prompt and f"pull request https://github.com/{REPO}/pull/7 at commit {HEAD}" in command.prompt
    assert "Continue on branch dispatch/x" in command.prompt and command.prompt not in command.argv
    audit = gh.comments[(REPO, n)][-1]["body"]
    assert audit.startswith(LAUNCHED_MARKER) and f"rework-of: {HEAD}" in audit and "reason: the review found a P1" in audit and "branch: dispatch/x" in audit
    assert (team_kit_home / "rework" / "meo-beo" / f"{n}.json").stat().st_mode & 0o077 == 0
    assert gh.prs[(REPO, 7)]["state"] == "OPEN" and len(gh.prs) == 1, "no new pull request"


@pytest.mark.parametrize(
    ("reviews", "inline", "ok"),
    [
        ([{"user": {"login": "alice", "type": "User"}, "state": "CHANGES_REQUESTED", "commit_id": HEAD}], [], True),
        ([], [{"user": {"login": "x[bot]", "type": "Bot"}, "commit_id": HEAD, "body": "P1"}], True),
        ([{"user": {"login": "x[bot]", "type": "Bot"}, "state": "APPROVED", "commit_id": HEAD}], [], False),
        ([{"user": {"login": "alice", "type": "User"}, "state": "COMMENTED", "commit_id": HEAD}], [], False),
        ([{**BOT_REVIEW, "commit_id": "c" * 40}], [], False),
        ([], [], False),
    ],
    ids=["changes-requested", "inline-app-comment", "approval", "human-comment", "older-head", "nothing"],
)
def test_a_changes_requested_review_or_an_app_finding_on_this_head_is_evidence_an_approval_is_not(team_kit_home, gh, capsys, reviews, inline, ok):
    n = reviewed(gh, with_finding=False)
    gh.reviews[7], gh.pr_comments_inline[7] = reviews, inline
    cloud = FakeCloud()
    assert (rework(gh, n, cloud=cloud) == 0) is ok
    assert len(cloud.started) == (1 if ok else 0)
    if not ok:
        assert "nothing to rework" in capsys.readouterr().err


def test_the_command_is_refused_in_a_turn_that_read_untrusted_text(team_kit_home, gh, capsys):
    n = reviewed(gh)
    marker = team_kit_home / "tainted-turns" / "meo-beo"
    marker.mkdir(parents=True)
    (marker / "s1").write_text(json.dumps({"session": "s1", "at": NOW - 60}))
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 1
    assert "read text strangers wrote" in capsys.readouterr().err
    assert cloud.started == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}


@pytest.mark.parametrize("bot", ["boot", "chief", "sep-crm"])
def test_only_the_projects_own_bot_may_run_it(team_kit_home, gh, capsys, bot):
    n = reviewed(gh)
    cloud = FakeCloud()
    assert run(gh, "rework", "--issue", str(n), "--pr", "7", "--head", HEAD, "--reason", "x", bot=bot, cloud=cloud) == 1
    assert cloud.started == []


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"head": "a" * 12}, "full 40-character"),
        ({"head": "A" * 40}, "full 40-character"),
        ({"head": "c" * 40}, "is at"),
        ({"pr_number": "8"}, "not a done report for pull request 8"),
        ({"reason": "   "}, "needs --reason"),
        ({"reason": "x" * 501}, "needs --reason"),
    ],
)
def test_a_caller_cannot_name_another_pull_request_or_a_stale_or_malformed_head(team_kit_home, gh, capsys, change, why):
    n = reviewed(gh)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud, **change) == 1
    assert why in capsys.readouterr().err
    assert cloud.started == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"} and len(gh.comments[(REPO, n)]) == 2


def test_another_pull_request_a_fork_a_closed_or_merged_pull_request_or_a_foreign_marker_is_refused(team_kit_home, gh, capsys):
    for prepare, why in (
        (lambda g, n: g.prs[(REPO, 7)].update(isCrossRepository=True, headRepo="mallory/shop"), "is not an open pull request"),
        (lambda g, n: g.prs[(REPO, 7)].update(state="CLOSED"), "is not an open pull request"),
        (lambda g, n: g.prs[(REPO, 7)].update(state="MERGED", mergedAt="2026-10-01T00:00:00Z"), "is not an open pull request"),
        (lambda g, n: g.prs[(REPO, 7)].update(body="no marker"), "is not an open pull request"),
        (lambda g, n: g.prs[(REPO, 7)].update(body=f"<!-- team-kit:pr v1 card=u issue={n + 1} -->"), "is not an open pull request"),
    ):
        fresh = FakeGh(json.loads(gh.files[".agents/dispatch.json"])["kit_ref"])
        n = reviewed(fresh)
        prepare(fresh, n)
        cloud = FakeCloud()
        assert rework(fresh, n, cloud=cloud) == 1
        assert why in capsys.readouterr().err
        assert cloud.started == [] and fresh.labels(REPO, n) == {"agent-task", "agent:review"}


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        ({"labels": ("agent-task", "agent:running")}, "already launched"),
        ({"labels": ("agent-task", "agent:verifying")}, "already launched"),
        ({"labels": ("agent-task", "agent:needs-human")}, "not waiting in agent:review"),
        ({"labels": ("agent-task",)}, "not waiting in agent:review"),
        ({"labels": ("agent:review",)}, "not an open agent-task"),
        ({"state": "closed"}, "not an open agent-task"),
        ({"author": "mallory"}, "not in trusted_dispatchers"),
        ({"body": "no marker here\n"}, "no team-kit brief marker"),
    ],
)
def test_only_an_open_trusted_unit_in_review_goes_back(team_kit_home, gh, capsys, kwargs, why):
    kwargs = {"labels": ("agent-task", "agent:review"), **kwargs}
    n = reviewed(gh, **kwargs)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 1
    assert why in capsys.readouterr().err
    assert cloud.started == []


def test_the_pull_request_must_be_the_one_in_the_units_latest_done_report(team_kit_home, gh, capsys):
    n = reviewed(gh)
    pr(gh, 9, issue=n, head=HEAD, branch="dispatch/x")
    finding(gh, 9)
    cloud = FakeCloud()
    assert rework(gh, n, pr_number="9", cloud=cloud) == 1
    assert "not a done report for pull request 9" in capsys.readouterr().err
    assert cloud.started == []


def test_a_report_that_is_not_done_or_is_missing_fails_closed(team_kit_home, gh, capsys):
    n = reviewed(gh)
    gh.comments[(REPO, n)].pop()
    gh.report(REPO, n, "partial", pr=f"https://github.com/{REPO}/pull/7")
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 1 and cloud.started == []


def test_an_untrusted_launch_or_report_comment_changes_nothing(team_kit_home, gh, capsys):
    n = reviewed(gh)
    gh.comment(REPO, n, f"{LAUNCHED_MARKER}\nrework-of: {HEAD}\n", login="mallory")
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 0 and len(cloud.started) == 1


def test_an_active_or_possible_verifier_blocks_it(team_kit_home, gh, capsys):
    n = reviewed(gh)
    record = team_kit_home / "verify" / "meo-beo" / f"{n}.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pr": f"https://github.com/{REPO}/pull/7", "head": HEAD, "key": "k", "backend": "claude", "started": NOW}))
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 1 and "verifier record" in capsys.readouterr().err and cloud.started == []


def test_a_second_request_made_while_the_first_is_starting_its_worker_starts_none(team_kit_home, gh, capsys):
    n = reviewed(gh)
    cloud = FakeCloud()
    rival = []
    start = cloud.start

    def starting(backend, repo, argv, workdir):
        rival.append(rework(gh, n, cloud=cloud))
        return start(backend, repo, argv, workdir)

    cloud.start = starting
    assert rework(gh, n, cloud=cloud) == 0
    assert rival == [1] and "already launched" in capsys.readouterr().err
    assert len(cloud.started) == 1 and tick(gh, capsys, cloud=cloud) == ["[SILENT]"] and len(cloud.started) == 1
    assert len([c for c in gh.comments[(REPO, n)] if "rework-of" in c["body"]]) == 1


def test_a_rival_that_takes_the_slot_between_the_first_check_and_the_lock_leaves_one_worker(team_kit_home, gh, capsys):
    n = reviewed(gh)
    cloud = FakeCloud()
    real = gh.__call__
    reads = []

    def racing(args, input=None):
        if args[0] == "api" and args[-1] == f"repos/{REPO}/issues/{n}":
            reads.append(1)
            if len(reads) == 2:
                gh.labels(REPO, n).discard("agent:review")
                gh.labels(REPO, n).add("agent:running")
        return real(args, input)

    assert rework(racing, n, cloud=cloud) == 1
    assert "already launched" in capsys.readouterr().err
    assert cloud.started == [] and gh.labels(REPO, n) == {"agent-task", "agent:running"}


def test_a_head_that_moves_between_the_first_check_and_the_lock_is_refused(team_kit_home, gh, capsys):
    n = reviewed(gh)
    cloud = FakeCloud()
    real = gh.__call__
    reads = []

    def moving(args, input=None):
        if args[0] == "api" and args[-1] == f"repos/{REPO}/issues/{n}":
            reads.append(1)
            if len(reads) == 2:
                gh.prs[(REPO, 7)]["headRefOid"] = NEW
        return real(args, input)

    assert rework(moving, n, cloud=cloud) == 1
    assert "is at" in capsys.readouterr().err and cloud.started == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}


def test_two_processes_racing_for_one_unit_start_one_worker(team_kit_home, gh, capsys):
    import threading

    n = reviewed(gh)
    cloud = FakeCloud()
    gate, both = threading.Barrier(2), []
    real = gh.__call__

    def meeting(args, input=None):
        if args[0] == "api" and args[-1] == f"repos/{REPO}/issues/{n}" and threading.current_thread().name.startswith("req") and not both:
            gate.wait(5)  # both requests have passed their first look at the unit before either takes the lock
            both.append(1)
        return real(args, input)

    codes = []
    threads = [threading.Thread(name=f"req{i}", target=lambda: codes.append(rework(meeting, n, cloud=cloud))) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert sorted(codes) == [0, 1] and len(cloud.started) == 1


def test_a_request_repeated_after_a_start_or_after_the_head_was_already_reworked_is_refused(team_kit_home, gh, capsys):
    n = reviewed(gh)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 0
    capsys.readouterr()
    assert rework(gh, n, cloud=cloud) == 1
    gh.labels(REPO, n).clear()
    gh.labels(REPO, n).update({"agent-task", "agent:review"})
    gh.report(REPO, n, "done", pr=f"https://github.com/{REPO}/pull/7")
    gh.comments[(REPO, n)][-1]["body"] = gh.comments[(REPO, n)][-1]["body"].replace("abc1234", "bbbbbbb")
    capsys.readouterr()
    assert rework(gh, n, cloud=cloud) == 1
    assert "already requested" in capsys.readouterr().err
    assert len(cloud.started) == 1


def test_a_new_head_with_new_findings_can_be_reworked_again(team_kit_home, kit, capsys):
    gh = FakeGh(kit, verifier_backend="none")
    n = reviewed(gh)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 0
    gh.prs[(REPO, 7)]["headRefOid"] = NEW
    gh.report(REPO, n, "done", pr=f"https://github.com/{REPO}/pull/7")
    gh.comments[(REPO, n)][-1]["body"] = gh.comments[(REPO, n)][-1]["body"].replace("abc1234", NEW[:7])
    tick(gh, capsys, cloud=cloud)
    assert gh.labels(REPO, n) == {"agent-task", "agent:review"}
    capsys.readouterr()
    assert rework(gh, n, head=NEW, cloud=cloud) == 1, "no finding on the new head yet"
    finding(gh, head=NEW)
    capsys.readouterr()
    assert rework(gh, n, head=NEW, cloud=cloud) == 0 and len(cloud.started) == 2


def stale_report(gh, n, head=HEAD):
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: done\nbranch: dispatch/x\nhead: {head[:7]}\npr: https://github.com/{REPO}/pull/7\n", login="Codertocat", at=NOW)


def test_a_delayed_report_of_the_old_head_never_moves_the_reworked_unit(team_kit_home, kit, capsys):
    gh = FakeGh(kit, verifier_backend="none")
    n = reviewed(gh)
    assert rework(gh, n, cloud=FakeCloud()) == 0
    capsys.readouterr()
    stale_report(gh, n)
    assert tick(gh, capsys) == ["[SILENT]"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:running"}
    gh.prs[(REPO, 7)]["headRefOid"] = NEW
    stale_report(gh, n, NEW)
    tick(gh, capsys)
    assert gh.labels(REPO, n) == {"agent-task", "agent:review"}


def test_a_report_without_a_head_cannot_end_a_rework(team_kit_home, gh, capsys):
    n = reviewed(gh)
    assert rework(gh, n, cloud=FakeCloud()) == 0
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: done\nbranch: dispatch/x\nhead: none\npr: https://github.com/{REPO}/pull/7\n", login="Codertocat", at=NOW)
    capsys.readouterr()
    assert tick(gh, capsys) == ["[SILENT]"] and gh.labels(REPO, n) == {"agent-task", "agent:running"}


def test_an_edited_or_forged_report_from_an_untrusted_login_is_ignored(team_kit_home, gh, capsys):
    n = reviewed(gh)
    assert rework(gh, n, cloud=FakeCloud()) == 0
    gh.prs[(REPO, 7)]["headRefOid"] = NEW
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: done\nbranch: dispatch/x\nhead: {NEW[:7]}\npr: https://github.com/{REPO}/pull/7\n", login="mallory")
    capsys.readouterr()
    assert tick(gh, capsys) == ["[SILENT]"] and gh.labels(REPO, n) == {"agent-task", "agent:running"}


def test_a_late_verifier_verdict_or_app_approval_of_the_old_head_does_not_approve_the_reworked_unit(team_kit_home, kit, capsys):
    gh = FakeGh(kit, review_app="claude[bot]", verifier_backend="none")
    n = reviewed(gh)
    app = team_kit_home / "app-reviews" / "meo-beo" / f"{n}.json"
    app.parent.mkdir(parents=True)
    app.write_text(json.dumps({"pr": f"https://github.com/{REPO}/pull/7", "head": HEAD, "outcome": "approved"}))
    gh.reviews[7].append({"user": {"login": "claude[bot]", "type": "Bot"}, "state": "APPROVED", "commit_id": HEAD})
    assert rework(gh, n, cloud=FakeCloud()) == 0
    assert not app.exists()
    capsys.readouterr()
    gh.comment(REPO, n, "<!-- team-kit:verdict v1 -->\nverification: live-ui-verified\nhead: " + HEAD + "\nsignature: 0\n", login="Codertocat", at=NOW)
    assert tick(gh, capsys) == ["[SILENT]"] and gh.labels(REPO, n) == {"agent-task", "agent:running"}
    gh.prs[(REPO, 7)]["headRefOid"] = NEW
    stale_report(gh, n, NEW)
    tick(gh, capsys)
    assert gh.merges == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}, "the old head's approval does not carry over"


def test_andon_slots_dependencies_and_the_kit_pin_still_gate_it_and_leave_the_unit_in_review(team_kit_home, kit, capsys):
    for config, extra, why in (
        ({"max_in_flight": 1}, "running", "repo limit"),
        ({}, "andon", "andon"),
    ):
        gh = FakeGh(kit, **config)
        n = reviewed(gh)
        if extra == "running":
            gh.add_issue(REPO, labels=("agent-task", "agent:running"))
        else:
            gh.add_issue(REPO, labels=("agent:andon",), title="Stop")
        cloud = FakeCloud()
        assert rework(gh, n, cloud=cloud) == 1
        assert why in capsys.readouterr().err.lower()
        assert cloud.started == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}
    gh = FakeGh(kit)
    n = reviewed(gh)
    gh.files[".agents/dispatch.json"] = json.dumps({"backend": "claude", "kit_ref": "0" * 40, "trusted_dispatchers": [BOT_LOGIN]})
    assert rework(gh, n, cloud=FakeCloud()) == 1 and "kit commit" in capsys.readouterr().err
    assert gh.labels(REPO, n) == {"agent-task", "agent:review"}


def test_a_dependency_that_is_open_holds_the_rework(team_kit_home, kit, capsys):
    gh = FakeGh(kit)
    dep = gh.add_issue(REPO)
    n = gh.add_issue(REPO, labels=("agent-task", "agent:review"), depends=f"{REPO}#{dep}")
    url = pr(gh, 7, issue=n, head=HEAD, branch="dispatch/x")
    gh.comment(REPO, n, f"{LAUNCHED_MARKER}\nsession: first\nbranch: dispatch/x\n", at=NOW - 900)
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: done\nbranch: dispatch/x\nhead: aaaaaaa\npr: {url}\n", login="Codertocat")
    finding(gh)
    cloud = FakeCloud()
    assert rework(gh, n, cloud=cloud) == 1 and "waiting for dependencies" in capsys.readouterr().err
    assert cloud.started == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}


def test_a_failed_start_returns_the_unit_to_review_and_the_request_can_be_retried(team_kit_home, gh, capsys):
    n = reviewed(gh)
    assert rework(gh, n, cloud=FakeCloud(fail="no session")) == 1
    assert "launch failed" in capsys.readouterr().err
    assert gh.labels(REPO, n) == {"agent-task", "agent:review"} and not (team_kit_home / "rework" / "meo-beo" / f"{n}.json").exists()
    assert len(gh.comments[(REPO, n)]) == 2, "nothing was recorded as launched"
    good = FakeCloud()
    assert rework(gh, n, cloud=good) == 0 and len(good.started) == 1
    counts = json.loads((team_kit_home / "in-flight" / "meo-beo.json").read_text())["repos"]
    assert counts == {REPO: 1}


def test_a_failed_audit_comment_still_bars_the_old_heads_reports_and_is_said(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__

    def no_comment(args, input=None):
        if args[:2] == ["issue", "comment"]:
            raise RuntimeError("rate limited")
        return real(args, input)

    assert rework(no_comment, n, cloud=FakeCloud()) == 0
    assert "audit" in stdout(capsys)
    stale_report(gh, n)
    assert tick(gh, capsys) == ["[SILENT]"] and gh.labels(REPO, n) == {"agent-task", "agent:running"}


def test_a_failed_relabel_leaves_no_record_and_the_unit_in_review(team_kit_home, gh, capsys):
    n = reviewed(gh)
    real = gh.__call__

    def no_edit(args, input=None):
        if args[:2] == ["issue", "edit"]:
            raise Refused("gh issue failed: 502")
        return real(args, input)

    assert rework(no_edit, n, cloud=FakeCloud()) == 1
    assert not (team_kit_home / "rework" / "meo-beo" / f"{n}.json").exists() and gh.labels(REPO, n) == {"agent-task", "agent:review"}


def test_stopped_units_still_launch_and_a_unit_in_review_still_refuses_plain_launch(team_kit_home, gh, capsys):
    stopped = gh.add_issue(REPO, labels=("agent-task", "agent:needs-human"))
    review = reviewed(gh)
    assert run(gh, "launch", "--issue", str(stopped)) == 0
    capsys.readouterr()
    assert run(gh, "launch", "--issue", str(review)) == 1
    assert "waits for the owner to merge" in capsys.readouterr().err
