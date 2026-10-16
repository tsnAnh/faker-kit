"""team-kit-gh work-events: queued GitHub events drive the same unit lifecycle tick runs, under one authority switch."""

import contextlib
import hashlib
import hmac
import io
import json
import secrets

import pytest
from team_kit_contracts import LAUNCHED_MARKER
from team_kit_event_queue import EventStore, State, default_db, receive
from team_kit_gh import EVENT_LEASE_SECONDS, main
from team_kit_guard.policy import tainted_turn_marker
from test_gh import HEAD, NOW, REPO, FakeCloud, FakeGh, app_review, pr

SECRET = secrets.token_hex(16).encode()
PROJECTS = {"acme/shop": "shop", "acme/crm": "crm"}
NEW_HEAD = "b" * 40
BASE = "c" * 40


class Queue:
    """The event queue a receiver filled, read back the way the worker reads it."""

    def __init__(self, home):
        self.store = EventStore(default_db(home))
        self.sent = 0

    def deliver(self, event, payload, delivery=None, at=NOW):
        self.sent += 1
        raw = json.dumps({**payload, "repository": {"full_name": REPO, "id": 101}, "sender": {"login": "someone"}}).encode()
        headers = {"X-GitHub-Delivery": delivery or f"d-{self.sent}", "X-GitHub-Event": event,
                   "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET, raw, hashlib.sha256).hexdigest()}
        answer = receive(raw, headers, SECRET, PROJECTS, self.store, at)
        assert answer.status == 202, answer
        return answer.delivery_id

    def state(self, delivery_id):
        return self.store.consumer_row(delivery_id, "lifecycle")


def pull_payload(number=7, head=HEAD, action="opened"):
    side = {"full_name": REPO, "id": 101}
    return {"action": action, "pull_request": {"number": number, "head": {"sha": head, "ref": "dispatch/x", "repo": side},
                                               "base": {"sha": BASE, "ref": "main", "repo": side}}}


def comment_payload(number, comment_id=9001):
    return {"action": "created", "issue": {"number": number}, "comment": {"id": comment_id}}


def issue_payload(number, action="closed"):
    return {"action": action, "issue": {"number": number}}


def check_payload(head=HEAD):
    return {"action": "completed", "check_run": {"id": 6001, "head_sha": head, "check_suite": {"id": 5001}}}


def review_payload(number=7, head=HEAD):
    return {"action": "submitted", "review": {"id": 7001}, **{k: v for k, v in pull_payload(number, head).items() if k != "action"}}


def command(gh, cloud, *args, now=NOW):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(args), run=gh, bot="meo-beo", cloud=cloud, now=lambda: now)
    assert code == 0, args
    return out.getvalue()


def work(gh, cloud, *flags, now=NOW):
    return command(gh, cloud, "work-events", *flags, now=now).strip().splitlines()


def tick(gh, cloud, now=NOW):
    return command(gh, cloud, "tick", now=now).strip().splitlines()


def authority(gh, action):
    return json.loads(command(gh, FakeCloud(), "authority", action))


def writes(gh):
    return [c for c in gh.calls if c[0] != "api"]


def decisions(home):
    path = home / "events" / "decisions" / "meo-beo.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def running_unit(gh, checks=("SUCCESS",), report=True):
    """A launched unit with its pull request open; with report, the agent's done report is on the issue."""
    n = gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    gh.comment(REPO, n, f"{LAUNCHED_MARKER}\nsession: s\nbackend: claude\nbranch: dispatch/x\n")
    url = pr(gh, 7, issue=n, checks=checks)
    if report:
        gh.report(REPO, n, "done", pr=url)
    return n


@pytest.fixture
def queue(team_kit_home):
    q = Queue(team_kit_home)
    yield q
    q.store.close()


# the lifecycle, driven by events ---------------------------------------------------------------------


def test_a_pull_request_opening_is_not_a_done_report(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh, report=False)
    authority(gh, "event")
    delivery = queue.deliver("pull_request", pull_payload())
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:running"}, "only the report comment moves a unit"
    assert cloud.started == [] and writes(gh) == []
    assert queue.state(delivery).state is State.DONE


def test_a_done_report_with_passing_checks_starts_the_verifier_as_tick_does(team_kit_home, kit, queue):
    polled, polled_cloud = FakeGh(kit), FakeCloud()
    by_tick = running_unit(polled)
    assert tick(polled, polled_cloud) == ["[SILENT]"]

    evented, evented_cloud = FakeGh(kit), FakeCloud()
    evented.next = 100
    by_event = running_unit(evented)
    authority(evented, "event")
    queue.deliver("issue_comment", comment_payload(by_event))
    assert work(evented, evented_cloud) == ["[SILENT]"]

    assert evented.labels(REPO, by_event) == polled.labels(REPO, by_tick) == {"agent-task", "agent:verifying"}
    assert [w.name for *_, w in evented_cloud.started] == [f"{by_event}-verify"]
    assert [w.name for *_, w in polled_cloud.started] == [f"{by_tick}-verify"]
    def shape(calls, n):
        return [[str(by_event) if a == str(n) else a for a in c[:5]] for c in calls]

    assert shape(writes(evented), by_event) == shape(writes(polled), by_tick), "the same writes, in the same order"


def test_checks_completing_later_advance_a_unit_that_waited_for_them(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh, checks=("PENDING",))
    authority(gh, "event")
    queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:running"} and cloud.started == []
    gh.prs[(REPO, 7)]["statusCheckRollup"] = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]
    queue.deliver("check_run", check_payload())
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:verifying"}
    assert len(cloud.started) == 1


def test_a_duplicate_event_starts_no_second_verifier_and_says_nothing_twice(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    first = queue.deliver("issue_comment", comment_payload(n), delivery="same-id")
    queue.deliver("issue_comment", comment_payload(n), delivery="same-id")
    queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == ["[SILENT]"]
    queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == ["[SILENT]"]
    assert len(cloud.started) == 1
    assert [c[:2] for c in writes(gh)] == [["issue", "edit"], ["issue", "comment"]], "one relabel and one verify-launched comment"
    assert queue.state(first).state is State.DONE


def test_a_duplicate_approval_event_merges_once_and_says_so_once(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit, verifier_backend="none", review_app="claude[bot]", auto_merge="low"), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == [f"team-kit notice: #{n} waits for claude[bot] to review https://github.com/{REPO}/pull/7 (checks passed)"]
    app_review(gh)
    queue.deliver("pull_request_review", review_payload())
    queue.deliver("pull_request_review", review_payload())
    assert work(gh, cloud) == [f"team-kit notice: #{n} merged automatically (low risk, checks passed, approved by claude[bot]): "
                               f"https://github.com/{REPO}/pull/7"]
    queue.deliver("pull_request_review", review_payload())
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.merges == [["pr", "merge", "7", "-R", REPO, "--merge", "--match-head-commit", HEAD]]


def test_a_stale_head_event_finds_no_work(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    running_unit(gh)
    gh.prs[(REPO, 7)]["headRefOid"] = NEW_HEAD
    authority(gh, "event")
    stale = [queue.deliver("pull_request", pull_payload(head=HEAD, action="synchronize")), queue.deliver("check_run", check_payload(HEAD))]
    assert work(gh, cloud) == ["[SILENT]"]
    assert writes(gh) == [] and cloud.started == []
    assert [queue.state(d).state for d in stale] == [State.SUPERSEDED, State.SUPERSEDED]
    assert not any(c[-1].startswith(f"repos/{REPO}/issues?") for c in gh.calls), "a stale event never reaches the lifecycle"


def test_an_approval_of_an_old_head_cannot_approve_the_new_head(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit, verifier_backend="none", review_app="claude[bot]", auto_merge="low"), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    queue.deliver("issue_comment", comment_payload(n))
    work(gh, cloud)
    app_review(gh, head=HEAD)
    gh.prs[(REPO, 7)]["headRefOid"] = NEW_HEAD
    old = queue.deliver("pull_request_review", review_payload(head=HEAD))
    queue.deliver("check_run", check_payload(NEW_HEAD))
    assert work(gh, cloud) == [f"team-kit notice: #{n} waits for claude[bot] to review https://github.com/{REPO}/pull/7 (checks passed)"]
    assert gh.merges == [] and gh.labels(REPO, n) == {"agent-task", "agent:review"}
    assert queue.state(old).state is State.SUPERSEDED


def test_a_merged_dependency_releases_the_queued_unit(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    dependency = gh.add_issue(REPO, labels=("agent-task", "agent:review"))
    queued = gh.add_issue(REPO, depends=f"#{dependency}")
    authority(gh, "event")
    queue.deliver("issue_comment", comment_payload(queued))
    assert work(gh, cloud) == ["[SILENT]"]
    assert cloud.started == [], "a comment releases nothing"
    queue.deliver("issues", issue_payload(queued, "labeled"))
    assert work(gh, cloud) == ["[SILENT]"]
    assert cloud.started == [], "its dependency is still open"
    gh.issues[(REPO, dependency)].update(state="closed", state_reason="completed")
    queue.deliver("issues", issue_payload(dependency, "closed"))
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, queued) == {"agent-task", "agent:running"}
    assert [w.name for *_, w in cloud.started] == [str(queued)]


def test_the_andon_cord_blocks_launches(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    queued = gh.add_issue(REPO)
    authority(gh, "event")
    command(gh, cloud, "andon", "raise", "stop", "the", "line")
    queue.deliver("issues", issue_payload(queued, "opened"))
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, queued) == {"agent-task"} and cloud.started == []


def test_a_restart_mid_run_resumes_without_doing_anything_twice(team_kit_home, kit, queue, monkeypatch):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    delivery = queue.deliver("issue_comment", comment_payload(n))

    class Crash(BaseException):
        pass

    def crash(*args, **kwargs):
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(EventStore, "complete", crash)
        with pytest.raises(Crash):
            work(gh, cloud)
    assert gh.labels(REPO, n) == {"agent-task", "agent:verifying"} and len(cloud.started) == 1
    assert queue.state(delivery).state is State.CLAIMED, "the side effect happened; the settle did not"
    assert work(gh, cloud) == ["[SILENT]"], "the lease still holds"
    assert work(gh, cloud, now=NOW + EVENT_LEASE_SECONDS + 1) == ["[SILENT]"]
    assert len(cloud.started) == 1 and [c[:2] for c in writes(gh)] == [["issue", "edit"], ["issue", "comment"]]
    assert queue.state(delivery).state is State.DONE


@pytest.mark.parametrize(("failure", "end"), [("gh api failed: gh: Server Error (HTTP 502)", State.DEAD),
                                              ("gh api failed: gh: Not Found (HTTP 404)", State.GONE)])
def test_a_failed_github_read_retries_then_dead_letters(team_kit_home, kit, queue, failure, end):
    gh, cloud = FakeGh(kit), FakeCloud()
    running_unit(gh)
    authority(gh, "event")
    delivery = queue.deliver("pull_request", pull_payload(action="synchronize"))
    gh.fail_paths[f"repos/{REPO}/pulls/7"] = failure
    seen = []
    for attempt in range(5):
        assert work(gh, cloud, now=NOW + attempt * 3600) == ["[SILENT]"]
        seen.append(queue.state(delivery).state)
    assert seen == [State.RETRYING] * 4 + [end]
    assert writes(gh) == [] and cloud.started == []
    assert work(gh, cloud, now=NOW + 6 * 3600) == ["[SILENT]"], "a dead letter is never claimed again"
    assert queue.state(delivery).attempts == 5


def test_a_lifecycle_read_that_fails_retries_the_event(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    delivery = queue.deliver("issue_comment", comment_payload(n))
    gh.fail_paths[f"repos/{REPO}/issues/{n}/comments"] = "gh api failed: gh: Server Error (HTTP 502)"
    assert work(gh, cloud) == ["[SILENT]"]
    row = queue.state(delivery)
    assert row.state is State.RETRYING and f"meo-beo#{n}" not in (row.reason or "") and f"{REPO}#{n}" in (row.reason or "")
    del gh.fail_paths[f"repos/{REPO}/issues/{n}/comments"]
    assert work(gh, cloud, now=NOW + 3600) == ["[SILENT]"]
    assert queue.state(delivery).state is State.DONE and gh.labels(REPO, n) == {"agent-task", "agent:verifying"}


def test_a_sweep_runs_the_time_based_gates_with_no_event(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh, report=False)
    gh.comments[(REPO, n)][0]["created_at"] = "2020-01-01T00:00:00Z"
    authority(gh, "event")
    assert work(gh, cloud) == ["[SILENT]"], "no event, no run"
    [notice] = work(gh, cloud, "--sweep")
    assert notice.startswith(f"team-kit notice: #{n} needs you: no report 24 hours after launch")


# one authority ---------------------------------------------------------------------------------------


def test_with_authority_poll_the_worker_only_logs_and_tick_acts(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    assert authority(gh, "show") == {"project": "shop", "authority": "poll"}
    delivery = queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == ["[SILENT]"]
    assert writes(gh) == [] and cloud.started == []
    assert gh.labels(REPO, n) == {"agent-task", "agent:running"}
    assert not (team_kit_home / "verify").exists(), "the read-only side wrote no record the acting side reads"
    assert queue.state(delivery).state is State.DONE
    logged = [(d["side"], d["acting"], d["action"]) for d in decisions(team_kit_home)]
    assert ("event", False, f"cloud start claude {n}-verify") in logged
    assert ("event", False, f"issue edit {n} +agent:verifying -agent:running") in logged

    assert tick(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:verifying"} and len(cloud.started) == 1
    assert ("tick", True, f"cloud start claude {n}-verify") in [(d["side"], d["acting"], d["action"]) for d in decisions(team_kit_home)]


def test_with_authority_event_tick_only_logs_and_the_worker_acts(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    queued = gh.add_issue(REPO)
    assert authority(gh, "event") == {"project": "shop", "authority": "event", "was": "poll"}
    assert tick(gh, cloud) == ["[SILENT]"]
    assert writes(gh) == [] and cloud.started == [], "tick neither steps the done unit nor launches the queued one"
    tick_logged = {d["action"] for d in decisions(team_kit_home) if d["side"] == "tick"}
    assert {f"cloud start claude {n}-verify", f"cloud start claude {queued}"} <= tick_logged
    assert all(not d["acting"] for d in decisions(team_kit_home))

    queue.deliver("issue_comment", comment_payload(n))
    queue.deliver("issues", issue_payload(queued, "opened"))
    assert work(gh, cloud) == ["[SILENT]"]
    assert gh.labels(REPO, n) == {"agent-task", "agent:verifying"}
    assert gh.labels(REPO, queued) == {"agent-task", "agent:running"}
    assert sorted(w.name for *_, w in cloud.started) == sorted([f"{n}-verify", str(queued)])


def test_rollback_restores_poll_and_tick_acts_again(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    assert authority(gh, "rollback") == {"project": "shop", "authority": "poll", "was": "event"}
    assert authority(gh, "show")["authority"] == "poll"
    queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == ["[SILENT]"] and cloud.started == []
    assert tick(gh, cloud) == ["[SILENT]"] and len(cloud.started) == 1


def test_only_the_command_changes_the_authority_and_never_after_untrusted_text(team_kit_home, kit, queue, capsys):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    authority(gh, "event")
    path = team_kit_home / "authority" / "meo-beo.json"
    before = path.read_bytes()
    for event, payload in [("issue_comment", comment_payload(n)), ("issues", issue_payload(n, "edited")), ("pull_request", pull_payload())]:
        queue.deliver(event, payload)
    work(gh, cloud)
    tick(gh, cloud)
    assert path.read_bytes() == before, "no event and no lifecycle step writes the authority"

    marker = tainted_turn_marker(team_kit_home, "meo-beo")
    marker.mkdir(parents=True)
    (marker / "s1").write_text(json.dumps({"session": "s1", "at": NOW - 5}))
    assert main(["authority", "rollback"], run=gh, bot="meo-beo", cloud=cloud, now=lambda: NOW) == 1
    assert "read text strangers wrote" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_a_garbled_authority_file_means_poll_for_both_sides(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    n = running_unit(gh)
    (team_kit_home / "authority").mkdir()
    (team_kit_home / "authority" / "meo-beo.json").write_text('{"authority": "both"}')
    queue.deliver("issue_comment", comment_payload(n))
    assert work(gh, cloud) == ["[SILENT]"] and cloud.started == []
    assert tick(gh, cloud) == ["[SILENT]"] and len(cloud.started) == 1


# the shadow comparison -----------------------------------------------------------------------------


def test_shadow_compare_reports_matches_and_mismatches_without_side_effects(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit), FakeCloud()
    evented = running_unit(gh)
    other = gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    gh.comment(REPO, other, f"{LAUNCHED_MARKER}\nsession: s\nbackend: claude\nbranch: dispatch/y\n")
    gh.report(REPO, other, "blocked")
    queue.deliver("issue_comment", comment_payload(evented))
    assert work(gh, cloud) == ["[SILENT]"], "poll authority: the worker only logs, and only for the unit its event named"
    tick(gh, cloud)

    snapshot = {p: p.read_bytes() for p in team_kit_home.rglob("*") if p.is_file()}
    calls = len(gh.calls)
    report = json.loads(command(gh, cloud, "shadow-compare", "--hours", "1"))
    assert {p: p.read_bytes() for p in team_kit_home.rglob("*") if p.is_file()} == snapshot
    assert len(gh.calls) == calls and cloud.started[1:] == []

    assert report["project"] == "shop" and report["authority"] == "poll"
    assert f"cloud start claude {evented}-verify" in report["matches"]
    assert f"issue edit {evented} +agent:verifying -agent:running" in report["matches"]
    assert f"issue edit {other} +agent:needs-human -agent:running" in report["only_tick"]
    assert report["only_event"] == []
    assert report["decisions"]["event"] >= 3 and report["decisions"]["tick"] >= 4


def test_shadow_compare_with_no_log_is_empty(team_kit_home, kit):
    report = json.loads(command(FakeGh(kit), FakeCloud(), "shadow-compare"))
    assert (report["matches"], report["only_tick"], report["only_event"]) == ([], [], [])
    assert report["decisions"] == {"tick": 0, "event": 0}


def test_the_decision_log_keeps_a_comments_marker_line_never_its_text(team_kit_home, kit, queue):
    gh, cloud = FakeGh(kit, verifier_backend="none"), FakeCloud()
    n = running_unit(gh)
    queue.deliver("issue_comment", comment_payload(n))
    work(gh, cloud)
    tick(gh, cloud)
    steps = [f"issue edit {n} +agent:review -agent:running", "pr edit 7 +risk:medium", "pr comment 7 <!-- team-kit:risk v1 -->"]
    notice = (f"notice team-kit notice: #{n} is ready for you to merge (checks passed, not independently verified, "
              f"risk medium (not independently verified)): https://github.com/{REPO}/pull/7")
    assert [(d["side"], d["acting"], d["action"]) for d in decisions(team_kit_home)] == \
        [("event", False, a) for a in [*steps, notice]] + [("tick", True, a) for a in [*steps, notice]]
    [risk_comment] = gh.pr_comments[7]
    written = (team_kit_home / "events" / "decisions" / "meo-beo.jsonl").read_text()
    assert all(line not in written for line in risk_comment.splitlines()[1:] if line.strip())
