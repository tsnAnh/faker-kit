import json
import threading
import time

import pytest
from team_kit_contracts import LAUNCHED_MARKER
from team_kit_gh import NO_CHECKS_GRACE_SECONDS, Gh
from team_kit_guard.policy import load_policy
from team_kit_review_request import REQUEST_ATTEMPTS, RETRY_SECONDS
from test_gh import BASE, BOT_LOGIN, HEAD, NOW, REPO, FakeCloud, FakeGh, pr, run, tick

NEW = "b" * 40
PR_URL = f"https://github.com/{REPO}/pull/7"
REQUEST = {"workflow": "claude-review.yml", "ref": "main", "inputs[pr]": "7", "inputs[head]": HEAD}


def app_gh(kit, **config):
    return FakeGh(kit, **{"verifier_backend": "none", "review_app": "claude[bot]", "auto_merge": "none", **config})


def done(gh, n, head=HEAD, pr_url=PR_URL, login=BOT_LOGIN, at=NOW - 600, status="done", branch="dispatch/x"):
    gh.comment(REPO, n, f"<!-- team-kit:report v1 -->\nstatus: {status}\nbranch: {branch}\nhead: {head}\npr: {pr_url}\n", login=login, at=at)


def final_unit(gh, labels=("agent-task", "agent:review"), head=HEAD, report=True, **pull):
    """A unit in review whose agent reported done on its pull request's exact current head, with passing checks."""
    n = gh.add_issue(REPO, labels=labels)
    gh.comment(REPO, n, f"{LAUNCHED_MARKER}\nsession: s\nbackend: claude\nbranch: dispatch/x\n", at=NOW - 900)
    pr(gh, 7, issue=n, head=head, branch="dispatch/x")
    gh.prs[(REPO, 7)].update(mergeable=True, **pull)
    if report:
        done(gh, n, head=head)
    return n


def request(gh, n, now=NOW):
    policy = load_policy()
    return Gh(policy, policy.bot("meo-beo"), gh, lambda: now).request_final_review({"number": n})


def saved(team_kit_home, n):
    return json.loads((team_kit_home / "review-requests" / "meo-beo" / f"{n}.json").read_text())


def push(gh, head):
    gh.prs[(REPO, 7)]["headRefOid"] = head


def test_a_valid_final_report_requests_exactly_one_review_of_that_head(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    assert request(gh, n) is None, "a request that GitHub accepts says nothing; check_app_review tells the owner it waits"
    assert gh.workflow_runs == [REQUEST]
    assert saved(team_kit_home, n) == {"pr": 7, "head": HEAD, "base": BASE, "state": "requested", "at": NOW, "attempts": 1}
    assert request(gh, n, now=NOW + 60) is None and gh.workflow_runs == [REQUEST], "a second tick requests nothing"


def test_a_duplicate_or_replayed_report_requests_nothing_more(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    request(gh, n)
    done(gh, n, at=NOW - 30)
    done(gh, n, at=NOW - 20)
    request(gh, n, now=NOW + 60)
    assert gh.workflow_runs == [REQUEST]


def test_a_report_for_an_older_head_after_the_head_moved_requests_nothing(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh, report=False)
    push(gh, NEW)
    done(gh, n, head=HEAD)
    assert request(gh, n) is None and gh.workflow_runs == []
    assert saved(team_kit_home, n)["skipped"] == f"#7 moved to {NEW[:12]} after the done report on {HEAD[:12]}"


def test_a_new_head_after_rework_needs_its_own_done_report_and_gets_its_own_request(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    request(gh, n)
    push(gh, NEW)
    assert request(gh, n, now=NOW + 60) is None and gh.workflow_runs == [REQUEST], "an intermediate push with no report asks for nothing"
    done(gh, n, head=NEW, at=NOW + 90)
    request(gh, n, now=NOW + 120)
    assert gh.workflow_runs == [REQUEST, {**REQUEST, "inputs[head]": NEW}]


def test_a_new_base_under_the_same_head_is_a_new_request(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    request(gh, n)
    gh.prs[(REPO, 7)]["baseRefOid"] = "2" * 40
    request(gh, n, now=NOW + 60)
    assert len(gh.workflow_runs) == 2


@pytest.mark.parametrize(("change", "why"), [
    ({"pr_url": f"https://github.com/{REPO}/pull/8"}, "#8 does not carry this unit's marker"),
    ({"pr_url": "https://github.com/mallory/shop/pull/7"}, "the done report names no pull request of this repository"),
    ({"head": HEAD[:7]}, "the done report names no full head commit"),
    ({"head": "z" * 40}, "the done report names no full head commit"),
    ({"head": HEAD.upper()}, "the done report names no full head commit"),
    ({"login": "Codertocat"}, "the latest report is not from a login in trusted_dispatchers"),
    ({"status": "partial"}, "the latest report is partial, not done"),
    ({"branch": "dispatch/other"}, "#7 is not on the unit's branch"),
])
def test_a_report_that_does_not_bind_this_unit_and_head_requests_nothing(team_kit_home, kit, change, why):
    gh = app_gh(kit)
    n = final_unit(gh, report=False)
    if change.get("pr_url", "").endswith("/8"):
        pr(gh, 8, head=HEAD, branch="dispatch/x")
    done(gh, n, **change)
    assert request(gh, n) is None and gh.workflow_runs == []
    assert saved(team_kit_home, n)["skipped"] == why


def test_a_report_from_a_login_outside_the_trusted_authors_is_never_read(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh, report=False)
    done(gh, n, login="mallory")
    request(gh, n)
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["skipped"] == "the latest report is none, not done"


@pytest.mark.parametrize(("pull", "why"), [
    ({"isCrossRepository": True, "headRepo": "mallory/shop"}, "#7 comes from a fork"),
    ({"body": "<!-- team-kit:pr v1 card=u issue=999 -->\nCloses #999\n"}, "#7 does not carry this unit's marker"),
    ({"body": "Closes the unit\n"}, "#7 does not carry this unit's marker"),
    ({"headRefName": "dispatch/other"}, "#7 is not on the unit's branch"),
    ({"baseRefName": "release"}, "#7 does not target the default branch"),
    ({"draft": True}, "#7 is a draft"),
    ({"state": "CLOSED"}, "#7 is closed"),
    ({"mergeable": False}, "#7 conflicts with its base branch"),
    ({"mergeable": None}, "GitHub is still checking #7 for conflicts"),
])
def test_a_pull_request_that_is_not_the_units_final_candidate_requests_nothing(team_kit_home, kit, pull, why):
    gh = app_gh(kit)
    n = final_unit(gh, report=False)
    if "body" in pull:
        pull = {"body": pull["body"].replace("999", str(n + 1000))}
    gh.prs[(REPO, 7)].update(pull)
    done(gh, n)
    request(gh, n)
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["skipped"] == why


@pytest.mark.parametrize(("checks", "why"), [
    (("FAILURE",), "checks are failed"),
    (("PENDING",), "checks are pending"),
    (("SUCCESS", "PENDING"), "checks are pending"),
])
def test_checks_that_have_not_passed_request_nothing(team_kit_home, kit, checks, why):
    gh = app_gh(kit)
    n = final_unit(gh)
    pr(gh, 7, issue=n, head=HEAD, branch="dispatch/x", checks=checks)
    gh.prs[(REPO, 7)]["mergeable"] = True
    request(gh, n)
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["skipped"] == why


def test_checks_github_never_started_request_nothing(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    gh.runs[HEAD] = [{"id": 5, "name": "ci", "status": "completed", "conclusion": "failure", "output": {"annotations_count": 1}}]
    gh.annotations[5] = [{"message": "The job was not started because recent account payments have failed"}]
    request(gh, n)
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["skipped"] == "checks are not started"


def test_no_checks_wait_for_the_grace_period_then_request(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    gh.runs[HEAD] = []
    request(gh, n)
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["skipped"] == "no checks have registered yet"
    request(gh, n, now=NOW - 600 + NO_CHECKS_GRACE_SECONDS)
    assert gh.workflow_runs == [REQUEST]


def test_a_pulled_andon_cord_requests_nothing(team_kit_home, kit, capsys):
    gh = app_gh(kit)
    n = final_unit(gh)
    assert run(gh, "andon", "raise", "stop") == 0
    capsys.readouterr()
    request(gh, n)
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["skipped"] == "the andon cord is pulled"


@pytest.mark.parametrize("labels", [("agent-task", "agent:running"), ("agent-task", "agent:needs-human"), ("agent-task", "agent:review", "agent:needs-human")])
def test_a_unit_not_waiting_in_review_requests_nothing(team_kit_home, kit, labels):
    gh = app_gh(kit)
    n = final_unit(gh, labels=labels)
    request(gh, n)
    assert gh.workflow_runs == []


def test_a_head_that_moves_between_the_read_and_the_request_is_refused(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    reads = []

    class Moving(FakeGh):
        def __call__(self, args, input=None):
            if args[0] == "api" and args[-1] == f"repos/{REPO}/pulls/7":
                reads.append(1)
                if len(reads) == 2:
                    push(self, NEW)
            return super().__call__(args, input)

    moving = Moving.__new__(Moving)
    moving.__dict__ = gh.__dict__
    assert request(moving, n) is None and gh.workflow_runs == []
    assert saved(team_kit_home, n)["skipped"] == "#7 moved before the request"


def test_two_concurrent_ticks_make_one_request(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)

    class Slow(FakeGh):
        def __call__(self, args, input=None):
            if args[0] == "api" and f"/issues/{n}/comments" in args[-1]:
                time.sleep(0.3)  # after the record is read: without the lock, both ticks would see no request yet
            return super().__call__(args, input)

    slow = Slow.__new__(Slow)
    slow.__dict__ = gh.__dict__
    results = []

    def tick_once():
        try:
            results.append(request(slow, n))
        except OSError as err:
            results.append(err)

    threads = [threading.Thread(target=tick_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [None, None] and gh.workflow_runs == [REQUEST]


def test_a_restart_between_the_request_and_its_record_never_repeats_it(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    path = team_kit_home / "review-requests" / "meo-beo" / f"{n}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pr": 7, "head": HEAD, "base": BASE, "state": "sending", "at": NOW - 60, "attempts": 0}))
    notice = request(gh, n)
    assert notice.startswith(f"team-kit notice: #{n}: a request for claude[bot] to review {PR_URL} at {HEAD[:12]} may not have reached GitHub")
    assert gh.workflow_runs == [] and saved(team_kit_home, n)["state"] == "lost"
    assert request(gh, n, now=NOW + RETRY_SECONDS) is None and gh.workflow_runs == [], "told once, never sent"


def test_a_refused_request_is_retried_a_bounded_number_of_times_and_never_approves(team_kit_home, kit, capsys):
    gh = app_gh(kit, auto_merge="low")
    n = final_unit(gh)
    gh.workflow_error = "gh api failed: HTTP 422: Workflow does not have 'workflow_dispatch' trigger"
    first = request(gh, n)
    assert first.startswith(f"team-kit notice: #{n}: GitHub refused to start claude[bot]'s review of {PR_URL} at {HEAD[:12]}")
    assert f"retrying up to {REQUEST_ATTEMPTS - 1} more times" in first

    def calls():
        return [c for c in gh.calls if c[-1].endswith("/dispatches")]

    assert request(gh, n, now=NOW + 60) is None and len(calls()) == 1, "a retry waits RETRY_SECONDS"
    assert request(gh, n, now=NOW + RETRY_SECONDS) is None and len(calls()) == 2
    last = request(gh, n, now=NOW + 2 * RETRY_SECONDS)
    assert len(calls()) == REQUEST_ATTEMPTS and last.startswith(f"team-kit notice: #{n}: gave up asking claude[bot] to review {PR_URL}")
    assert request(gh, n, now=NOW + 10 * RETRY_SECONDS) is None and len(calls()) == REQUEST_ATTEMPTS
    assert saved(team_kit_home, n)["state"] == "failed"
    assert tick(gh, capsys) == [f"team-kit notice: #{n} waits for claude[bot] to review {PR_URL} (checks passed)"]
    assert gh.merges == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}


def test_a_retry_that_github_accepts_is_recorded_as_requested(team_kit_home, kit):
    gh = app_gh(kit)
    n = final_unit(gh)
    gh.workflow_error = "gh api failed: HTTP 502"
    request(gh, n)
    gh.workflow_error = None
    request(gh, n, now=NOW + RETRY_SECONDS)
    assert gh.workflow_runs == [REQUEST] and saved(team_kit_home, n)["state"] == "requested"


def test_comment_text_never_starts_a_request(team_kit_home, kit, capsys):
    gh = app_gh(kit)
    n = final_unit(gh, report=False)
    gh.comment(REPO, n, f"@claude review {PR_URL} at {HEAD}\nstatus: done\nhead: {HEAD}\npr: {PR_URL}\n")
    gh.pr_comment(f"/review {HEAD}", login=BOT_LOGIN, association="OWNER")
    gh.calls.clear()
    request(gh, n)
    assert gh.workflow_runs == []
    assert not [c for c in gh.calls if "/pulls/7/comments" in c[-1] or "/issues/comments" in c[-1] or "/pulls/comments" in c[-1]], \
        "pull request comments are never read"


def test_a_repository_without_review_app_never_requests(team_kit_home, kit, capsys):
    gh = FakeGh(kit, verifier_backend="none")
    n = final_unit(gh)
    tick(gh, capsys)
    assert request(gh, n) is None and gh.workflow_runs == []


def test_demonstration_one_review_for_the_final_head_through_tick(team_kit_home, kit, capsys):
    """End to end through tick: an intermediate head asks for nothing, the final done report asks for one review,
    a moved head asks for nothing and waits, and a duplicate is refused."""
    gh = app_gh(kit)
    n = final_unit(gh, labels=("agent-task", "agent:running"), report=False)
    cloud = FakeCloud()
    push(gh, NEW)
    assert tick(gh, capsys, cloud=cloud) == ["[SILENT]"] and gh.workflow_runs == [], "intermediate head"
    push(gh, HEAD)
    done(gh, n)
    assert tick(gh, capsys, cloud=cloud) == [f"team-kit notice: #{n} waits for claude[bot] to review {PR_URL} (checks passed)"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:review"}
    assert tick(gh, capsys, cloud=cloud) == ["[SILENT]"] and gh.workflow_runs == [REQUEST], "final done report, one request"
    push(gh, NEW)
    assert tick(gh, capsys, cloud=cloud) == [f"team-kit notice: #{n} waits for claude[bot] to review {PR_URL} (checks passed)"]
    assert gh.workflow_runs == [REQUEST] and gh.merges == [], "moved head: nothing requested, the unit waits"
    push(gh, HEAD)
    done(gh, n, at=NOW - 10)
    tick(gh, capsys, cloud=cloud)
    assert gh.workflow_runs == [REQUEST], "duplicate refused"


def test_a_broken_request_never_stops_the_review_gate(team_kit_home, kit, capsys, monkeypatch):
    gh = app_gh(kit)
    n = final_unit(gh)
    gh.prs[(REPO, 7)]["mergeable"] = False

    def broken(self, issue):
        raise OSError("disk full")

    monkeypatch.setattr(Gh, "request_final_review", broken)
    notices = tick(gh, capsys)
    assert notices[0] == (f"team-kit notice: #{n} needs you: {PR_URL} has merge conflicts with its base branch; relaunch it to resolve them. "
                          f"https://github.com/{REPO}/issues/{n}")
    assert gh.labels(REPO, n) == {"agent-task", "agent:needs-human"}
