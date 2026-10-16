"""team-kit-gh reads GitHub only through Gh.api_page / Gh.api_json: paging, ETag reuse, and failures."""
import json

import pytest
import team_kit_gh
from team_kit_contracts import LAUNCHED_MARKER
from team_kit_gh import InFlight, Refused
from team_kit_guard.policy import load_policy
from test_gh import BOT_LOGIN, HEAD, NOW, REPO, FakeCloud, FakeGh, gh_for, iso, pr, run, tick


@pytest.fixture
def gh(kit):
    return FakeGh(kit)


@pytest.fixture
def bot(team_kit_home, gh):
    return gh_for(team_kit_home, gh, "meo-beo")


def requests(gh):
    return [call[-1] for call in gh.calls if call[0] == "api"]


@pytest.mark.parametrize("argv", [
    ["issue", "list", "-R", REPO, "--label", "agent-task", "--json", "number"],
    ["issue", "view", "4", "-R", REPO, "--comments"],
    ["issue", "status", "-R", REPO],
    ["pr", "list", "-R", REPO, "--head", "dispatch/x"],
    ["pr", "view", "7", "-R", REPO, "--json", "state"],
    ["pr", "status", "-R", REPO],
    ["pr", "checks", "7", "-R", REPO],
    ["api", "--paginate", f"repos/{REPO}/issues"],
])
def test_the_double_rejects_graphql_backed_reads_and_gh_paginate(gh, argv):
    with pytest.raises(AssertionError):
        gh(argv)


def test_in_flight_reads_every_page_and_counts_a_unit_with_both_labels_once(team_kit_home, gh, bot):
    gh.page_size = 2
    gh.add_issue(REPO, labels=("agent-task", "agent:running", "agent:verifying"))
    for _ in range(3):
        gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    gh.add_issue(REPO, labels=("agent-task", "agent:verifying"))
    gh.add_issue(REPO, labels=("agent-task", "agent:running"), pull_request=True)
    gh.add_issue(REPO, labels=("agent-task", "agent:running"), state="closed")
    assert bot.in_flight() == 5
    assert any("labels=agent%3Arunning" in r and "page=2" in r for r in requests(gh)), "the running label took more than one page"
    assert any("labels=agent%3Averifying" in r for r in requests(gh))
    assert not any(c[:2] in (["issue", "list"], ["pr", "view"]) for c in gh.calls)


def test_count_own_publishes_the_count_with_the_supplied_clock(team_kit_home, gh):
    gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    policy = load_policy()
    counter = InFlight(policy, policy.bot("meo-beo"), gh, lambda: NOW)
    assert counter.count_own() == {REPO: 1}
    assert json.loads((team_kit_home / "in-flight" / "meo-beo.json").read_text()) == {"at": NOW, "repos": {REPO: 1}}


def test_an_idle_in_flight_count_revalidates_every_page_and_does_no_new_work(team_kit_home, gh, bot):
    gh.page_size = 2
    for _ in range(5):
        gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    assert bot.in_flight() == 5
    before, seen = gh.work, len(gh.calls)
    assert bot.in_flight() == 5
    assert gh.work == before
    revalidated = [c for c in gh.calls[seen:] if c[0] == "api"]
    assert revalidated and all(any(a.startswith("If-None-Match: ") for a in c) for c in revalidated)


def test_a_failed_page_leaves_no_partial_count(team_kit_home, gh):
    gh.page_size = 2
    for _ in range(5):
        gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    gh.fail_paths = {"page=2": "gh api failed: HTTP 502"}
    policy = load_policy()
    with pytest.raises(Refused, match="HTTP 502"):
        InFlight(policy, policy.bot("meo-beo"), gh, lambda: NOW).count_own()
    assert not (team_kit_home / "in-flight" / "meo-beo.json").exists()


def test_a_pull_request_in_the_issue_feed_is_never_a_unit(team_kit_home, gh, bot):
    gh.add_issue(REPO, labels=("agent-task",), pull_request=True)
    real = gh.add_issue(REPO, labels=("agent-task",))
    assert [i["number"] for i in bot.units()] == [real]


def test_closed_units_are_read_with_since_and_every_page(team_kit_home, gh, bot):
    gh.page_size = 2
    old = gh.add_issue(REPO, state="closed", closed_at=iso(NOW - 7200))
    recent = [gh.add_issue(REPO, state="closed", closed_at=iso(NOW - 60 + i)) for i in range(3)]
    found = [i["number"] for i in bot.units("closed", since=iso(NOW - 600))]
    assert found == recent and old not in found
    assert any("since=" in r and "page=2" in r for r in requests(gh))


@pytest.mark.parametrize(("checks", "statuses", "expected"), [
    ((), [], "none"),
    (("SUCCESS",), [], "passed"),
    (("SUCCESS", "NEUTRAL", "SKIPPED"), [], "passed"),
    (("SUCCESS", "FAILURE"), [], "failed"),
    (("CANCELLED",), [], "failed"),
    (("TIMED_OUT",), [], "failed"),
    (("SUCCESS", "PENDING"), [], "pending"),
    (("PENDING", "FAILURE"), [], "failed"),
    ((), [{"state": "success"}], "passed"),
    ((), [{"state": "pending"}], "pending"),
    ((), [{"state": "failure"}], "failed"),
    ((), [{"state": "error"}], "failed"),
    (("SUCCESS",), [{"state": "failure"}], "failed"),
    (("SUCCESS",), [{"state": "pending"}], "pending"),
    (("SUCCESS",), [{"state": "success"}], "passed"),
])
def test_pr_state_maps_check_runs_and_legacy_statuses(gh, bot, checks, statuses, expected):
    pr(gh, 7, checks=checks)
    gh.statuses[HEAD] = statuses
    assert bot.pr_state(7) == {"pr": 7, "state": "open", "merged": False, "head": HEAD, "checks": expected, "conflicts": False}


@pytest.mark.parametrize(("state", "merged", "words"), [("OPEN", False, "open"), ("CLOSED", False, "closed"), ("MERGED", True, "merged")])
def test_pr_state_names_open_closed_and_merged(gh, bot, state, merged, words):
    pr(gh, 7, state=state, merged=merged)
    assert bot.pr_state(7)["state"] == words and bot.pr_state(7)["merged"] is merged


def test_a_failing_check_on_a_later_page_still_fails_the_pull_request(gh, bot):
    gh.page_size = 2
    pr(gh, 7, checks=("SUCCESS", "SUCCESS", "SUCCESS", "FAILURE"))
    assert bot.pr_state(7)["checks"] == "failed"
    pr(gh, 8, checks=("SUCCESS", "SUCCESS", "SUCCESS", "PENDING"), head="b" * 40)
    assert bot.pr_state(8)["checks"] == "pending"


def test_pr_state_revalidates_and_sees_a_check_change(gh, bot):
    pr(gh, 7, checks=("PENDING",))
    assert bot.pr_state(7)["checks"] == "pending"
    before = gh.work
    assert bot.pr_state(7)["checks"] == "pending"
    assert gh.work == before, "unchanged checks come back 304 and cost nothing"
    pr(gh, 7, checks=("SUCCESS",))
    assert bot.pr_state(7)["checks"] == "passed"
    assert gh.work > before


@pytest.mark.parametrize("failing", ["/pulls/7", "check-runs", "/status"])
def test_a_failed_pull_request_read_raises_instead_of_guessing_a_state(gh, bot, failing):
    pr(gh, 7)
    gh.fail_paths = {failing: "gh api failed: HTTP 502"}
    with pytest.raises(Refused, match="HTTP 502"):
        bot.pr_state(7)


def test_a_failed_check_read_launches_nothing_and_gates_nothing(team_kit_home, gh, capsys):
    n = gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    gh.report(REPO, n, "done", pr=pr(gh, 7))
    gh.fail_paths = {"check-runs": "gh api failed: HTTP 502"}
    cloud = FakeCloud()
    [notice] = tick(gh, capsys, cloud=cloud)
    assert "tick could not handle" in notice and "HTTP 502" in notice
    assert gh.labels(REPO, n) == {"agent-task", "agent:running"}
    assert cloud.started == []


def test_the_unit_pr_search_by_branch_reads_every_page_of_open_pull_requests(team_kit_home, gh, bot):
    gh.page_size = 2
    n = gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    for number in range(1, 5):
        pr(gh, number, issue=999, branch="dispatch/x")
    pr(gh, 5, issue=n, branch="dispatch/x")
    pr(gh, 6, issue=n, branch="dispatch/y")
    pr(gh, 7, issue=n, branch="dispatch/x", state="CLOSED")
    launch = {"user": {"login": BOT_LOGIN}, "body": f"{LAUNCHED_MARKER}\nsession: s\nbackend: claude\nbranch: dispatch/x\n", "created_at": iso(NOW - 600)}
    assert bot.unit_pr(n, [launch]) == {"pr": 5, "url": f"https://github.com/{REPO}/pull/5", "head": HEAD, "branch": "dispatch/x"}
    listing = [r for r in requests(gh) if "/pulls?" in r]
    assert listing and all("head=acme%3Adispatch%2Fx" in r and "state=open" in r for r in listing)
    assert any("page=3" in r for r in listing)


def test_a_fork_pull_request_is_never_the_units_pr(gh, bot):
    n = gh.add_issue(REPO, labels=("agent-task", "agent:running"))
    pr(gh, 9, issue=n, branch="dispatch/x", fork=True)
    launch = {"user": {"login": BOT_LOGIN}, "body": f"{LAUNCHED_MARKER}\nsession: s\nbackend: claude\nbranch: dispatch/x\n", "created_at": iso(NOW - 600)}
    gh.report(REPO, n, "partial", pr=f"https://github.com/{REPO}/pull/9")
    assert bot.unit_pr(n, [launch, *gh.comments[(REPO, n)]]) is None


def test_comments_pages_are_joined_in_order(gh, bot):
    gh.page_size = 2
    n = gh.add_issue(REPO)
    for i in range(5):
        gh.comment(REPO, n, f"comment {i}", at=NOW - 100 + i)
    assert [c["body"] for c in bot.comments(n)] == [f"comment {i}" for i in range(5)]
    assert sum("comments?" in r for r in requests(gh)) == 3


def test_pull_request_files_are_read_to_the_last_page(gh, bot):
    gh.page_size = 2
    gh.pr_files[7] = [{"filename": f"f{i}.py", "status": "modified", "additions": 1, "deletions": 0} for i in range(5)]
    assert [f["filename"] for f in bot.api_pages(f"repos/{REPO}/pulls/7/files")] == [f"f{i}.py" for i in range(5)]


def test_a_collection_that_never_ends_is_refused_not_truncated(gh, bot, monkeypatch):
    monkeypatch.setattr(team_kit_gh, "ISSUE_PAGE_LIMIT", 2)
    gh.page_size = 2
    for _ in range(5):
        gh.add_issue(REPO)
    with pytest.raises(Refused, match="more than"):
        bot.units()


def test_a_collection_response_that_is_not_a_list_is_refused(gh, bot):
    with pytest.raises(Refused, match="no list"):
        bot.api_pages(f"repos/{REPO}")


def test_the_collaborator_permission_check_revalidates_and_fails_closed(gh, bot):
    gh.permissions = {"dev": "write"}
    issue = {"user": {"login": "dev"}, "author_association": "NONE"}
    assert bot.collaborator(issue) is True
    before = gh.work
    assert bot.collaborator(issue) is True and gh.work == before
    gh.fail_permissions = True
    assert bot.collaborator({"user": {"login": "other"}, "author_association": "NONE"}) is None


def test_read_renders_an_issue_and_a_pull_request_with_their_comments(gh, bot):
    n = gh.add_issue(REPO, title="Export CSV", body="Build the export.")
    gh.comment(REPO, n, "Looks good", login="teammate")
    text = bot.read(n, None)
    assert text.startswith("title:\tExport CSV\nstate:\tOPEN\nauthor:\tmeo-beo-gh\n--\nBuild the export.\n")
    assert "teammate" in text and "Looks good" in text
    pr(gh, 7, issue=n)
    gh.comment(REPO, 7, "ship it", login="reviewer")
    assert "ship it" in bot.read(None, 7) and "state:\tOPEN" in bot.read(None, 7)


def test_the_pr_state_command_reads_through_rest(team_kit_home, gh, capsys):
    pr(gh, 7, checks=("SUCCESS",))
    assert run(gh, "pr-state", "--pr", "7") == 0
    assert json.loads(capsys.readouterr().out) == {"pr": 7, "state": "open", "merged": False, "head": HEAD, "checks": "passed", "conflicts": False}
    assert not any(c[0] in ("pr", "issue") for c in gh.calls)

