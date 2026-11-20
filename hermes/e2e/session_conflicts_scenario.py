from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, PROJECT, Board, ledger
from session_summary_scenario import claude_calls, writes

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

SESSION = "session_01E2Eabc"
BROKEN = "session_01E2Efail"
FOOTER = "---- faker-kit machine fields (read by the kit, do not edit) ----"
DISPATCH_SKILL = Path(__file__).resolve().parent.parent / "skills" / "dispatch" / "SKILL.md"
WORDING = "reads each unit pull request's conflict state before any check"
CHECK_READS = ("check-runs", "/statuses", "/reviews", "/timeline", "/actions/runs")


def sha(number: int, version: int = 0) -> str:
    return f"{number:030x}{version:010x}"


def stamp(run: Run, minutes_ago: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(run.clock - minutes_ago * 60))


def conflicts_scenario(run: Run) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)

    def project(*argv: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT, env=env)

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def pull(number: int, unit: str | None, **fields: object) -> dict:
        text = f"Unit: {unit}\n\nChange for {unit}" if unit else "A change with no unit line"
        return {**run.pull(number, sha(number), mergeable=fields.pop("mergeable", False)), "body": text, **fields}

    def add_text(unit: str, line: str) -> None:
        board.edit_card(key(unit), body=board.body(key(unit)).replace(f"\n\n{FOOTER}", f"\n{line}\n\n{FOOTER}", 1))

    def add_footer(unit: str, line: str) -> None:
        board.edit_card(key(unit), body=board.body(key(unit)).rstrip("\n") + f"\n{line}")

    def set_pull(number: int, **fields: object) -> None:
        run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == number).update(fields))

    def gh_lines() -> list[str]:
        return run.gh_log.read_text().splitlines() if run.gh_log.exists() else []

    def reads_of(number: int, lines: list[str]) -> int:
        return sum(1 for line in lines if json.loads(line)[-1] == f"repos/acme/shop/pulls/{number}")

    def sent() -> list[dict]:
        return [json.loads(line) for line in run.claude_log.read_text().splitlines()] if run.claude_log.exists() else []

    def pass_over(*argv: str) -> dict[int, dict]:
        done = project("session", "conflicts", *argv)
        return {row["pr"]: row for row in json.loads(done[1])["conflicts"]} if done[0] == 0 else {}

    assert run.kit("loop", "migrate", "--apply", bot=CHIEF)[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0

    cases: list[tuple[int, str | None, dict, str | None]] = [
        (21, "conflict-pending-ci", {}, SESSION), (22, "conflict-green-ci", {}, SESSION), (23, "conflict-red-ci", {}, SESSION),
        (24, "clean-pending-ci", {"mergeable": True}, SESSION), (25, "clean-green-ci", {"mergeable": True}, SESSION),
        (26, "unknown-forever", {"mergeable": None}, SESSION), (27, "conflict-draft", {"draft": True}, SESSION),
        (28, "behind-base", {"mergeable": True, "mergeable_state": "behind"}, SESSION), (29, "blocked-state", {"mergeable": True, "mergeable_state": "blocked"}, SESSION),
        (30, "unstable-state", {"mergeable": True, "mergeable_state": "unstable"}, SESSION),
        (31, "followed-up-already", {}, SESSION), (32, "new-head-conflicts", {}, SESSION), (33, "silent-after-followup", {}, SESSION), (34, "inside-timeout", {}, SESSION),
        (35, "third-conflict", {}, SESSION), (36, "no-session-recorded", {}, None), (37, None, {}, SESSION), (38, "unlabeled-unit", {"labels": []}, SESSION),
        (40, "bound-elsewhere", {}, SESSION), (41, "from-a-fork", {"head_repo": "fork/shop"}, SESSION), (42, "other-bots-card", {}, SESSION),
        (44, "closes-between-reads", {"mergeable": None}, SESSION), (46, "delivery-fails", {}, BROKEN),
    ]
    run.update_gh(lambda state: state["pulls"].extend(pull(n, unit, **extra) for n, unit, extra, _ in cases))
    run.update_gh(lambda state: state["pulls"].extend([pull(39, "no-card-at-all"), pull(43, "closed-unit", state="closed"),
                                                      pull(47, "merged-unit", state="closed", merged_at="2026-10-20T00:00:00Z")]))
    run.update_gh(lambda state: state["check_runs"].update({sha(21): [{"name": "ci", "conclusion": None}], sha(22): [{"name": "ci", "conclusion": "success"}],
                                                            sha(23): [{"name": "ci", "conclusion": "failure"}], sha(24): [{"name": "ci", "conclusion": None}],
                                                            sha(25): [{"name": "ci", "conclusion": "success"}]}))
    for number, unit, _, session in cases:
        if unit is None:
            continue
        assert project("loop", "unit", unit, "--title", f"Visitors see change {number}", "--pr", str(24 if unit == "bound-elsewhere" else number))[0] == 0
        if session:
            add_footer(unit, f"Session: https://claude.ai/code/{session}")
    board.edit_card(key("other-bots-card"), assignee="gone-bot")
    add_text("followed-up-already", f"Conflict follow-up: head {sha(31)} {stamp(run, 5)}")
    add_text("new-head-conflicts", f"Conflict follow-up: head {sha(32, 9)} {stamp(run, 90)}")
    add_text("silent-after-followup", f"Conflict follow-up: head {sha(33)} {stamp(run, 46)}")
    add_text("inside-timeout", f"Conflict follow-up: head {sha(34)} {stamp(run, 44)}")
    add_text("third-conflict", f"Conflict follow-up: head {sha(35, 8)} {stamp(run, 300)}")
    add_text("third-conflict", f"Conflict follow-up: head {sha(35, 9)} {stamp(run, 120)}")
    run.on_sleep = [lambda: set_pull(44, state="closed", mergeable=False)]

    chief = run.kit("session", "conflicts", bot=CHIEF)
    run.step("session conflicts: only a project bot runs it", chief[0] == 1 and "not one of its commands" in chief[2], chief[2].strip())
    named = project("session", "conflicts", "12")
    bare = project("session", "steer")
    run.step("session conflicts: it takes no pull request or unit, and steer and summary still need theirs",
             named[0] == 1 and "takes no unit" in named[2] and bare[0] == 1 and "takes a unit id or a pull request number" in bare[2], (named[2] + bare[2]).strip())

    mutations, before_claude, before_writes, before_gh, sleeps = board.mutations(), claude_calls(run), len(writes(run)), len(gh_lines()), len(run.sleeps)
    rows = pass_over("--dry-run")
    seen = gh_lines()[before_gh:]
    wants = {21: "would steer", 22: "would steer", 23: "would steer", 27: "would steer", 32: "would steer", 46: "would steer", 24: "none", 25: "none", 26: "unsettled", 28: "none",
             29: "none", 30: "none", 31: "none", 34: "none", 33: "tell-owner", 35: "tell-owner", 36: "tell-owner", 44: "none", 37: "none", 38: "none", 39: "none", 40: "none",
             41: "none", 42: "none"}
    got = {n: rows.get(n, {}).get("action") for n in wants}
    run.step("session conflicts --dry-run: a conflicting pull request is followed up whether its checks are pending, green or red and whether it is a draft or ready; a clean one is left alone "
             "(pending or green), and so are behind, blocked and unstable ones",
             got == wants and rows[27]["draft"] is True and rows[21]["decision"] == "steer" and rows[24]["decision"] == "none", json.dumps(got))
    run.step("session conflicts --dry-run: an unknown pull request is re-polled, never read as clean, and given up after three reads and two five-second pauses; the next run reads it again",
             rows[26]["decision"] == "repoll" and reads_of(26, seen) == 3 and run.sleeps[sleeps:] == [5.0, 5.0], f"{rows[26]} reads={reads_of(26, seen)} sleeps={run.sleeps[sleeps:]}")
    run.step("session conflicts --dry-run: a pull request that closes between two reads is read twice and then left alone",
             rows[44]["decision"] == "none" and reads_of(44, seen) == 2, json.dumps(rows[44]))
    run.step("session conflicts --dry-run: the follow-up recorded for this head stops a second one, a new head conflicting after an older follow-up gets one, a head still conflicting "
             "45 minutes after its follow-up goes to the owner, and so does a third conflicting head after two follow-ups",
             rows[31]["decision"] == "none" and rows[32]["decision"] == "steer" and rows[34]["decision"] == "none"
             and rows[33]["line"] == f"PR #33 still conflicting after the follow-up, head {sha(33)}" and rows[35]["line"] == f"PR #35 conflicting again after 2 follow-ups, head {sha(35)}",
             json.dumps([rows[33], rows[35]]))
    run.step("session conflicts --dry-run: a conflicting unit with no recorded session is reported for the owner and nothing is sent",
             rows[36]["line"] == f"PR #36 conflicts with main and its unit has no session to ask, head {sha(36)}", json.dumps(rows[36]))
    reasons = {n: rows[n].get("reason", "") for n in (37, 38, 39, 40, 41, 42)}
    run.step("session conflicts --dry-run: a pull request that is not ours is skipped with its reason: no Unit line, no faker-kit label, no unit card, a card bound to another pull request, "
             "a fork, a card of another bot; a closed or merged one is not listed",
             "Unit" in reasons[37] and "not labeled" in reasons[38] and "bound" in reasons[39] and "bound" in reasons[40] and "fork" in reasons[41] and "bound" in reasons[42]
             and 43 not in rows and 47 not in rows, json.dumps(reasons))
    touched = [n for n in (37, 38, 39, 40, 41, 42, 43, 47) if reads_of(n, seen)]
    run.step("session conflicts --dry-run: a pull request that is not ours, closed or merged is never read in detail", touched == [], f"read: {touched}")
    run.step("session conflicts --dry-run: sends no message, edits no card, writes nothing to GitHub, and reads no check, status, review, timeline or workflow run",
             claude_calls(run) == before_claude and board.mutations() == mutations and len(writes(run)) == before_writes
             and not any(word in line for line in seen for word in CHECK_READS), f"calls={claude_calls(run) - before_claude} mutations={board.mutations() - mutations}")
    paths = [json.loads(line)[-1] for line in seen]
    run.step("session conflicts: the pass lists the open pull requests first, then reads each one's conflict state",
             paths[0].startswith("repos/acme/shop/pulls?") and "repos/acme/shop/pulls/21" in paths, " ".join(paths[:3]))
    skill = DISPATCH_SKILL.read_text()
    run.step("the dispatch skill says once that the kit reads conflict state before any check and sends the first follow-up, and names the gate's line, matching the pass above",
             skill.count(WORDING) == 1 and "`faker-kit session conflicts:`" in skill, f"{skill.count(WORDING)} occurrence(s)")

    run.update_gh(lambda state: state["pulls"].append(pull(45, "unknown-then-dirty", mergeable=None, mergeable_reads=[None, False])))
    assert project("loop", "unit", "unknown-then-dirty", "--title", "Visitors see change 45", "--pr", "45")[0] == 0
    add_footer("unknown-then-dirty", f"Session: https://claude.ai/code/{SESSION}")
    mutations, before_claude, sleeps, before_gh = board.mutations(), claude_calls(run), len(run.sleeps), len(gh_lines())
    live = pass_over()
    calls = sent()[before_claude:]
    steered = sorted(n for n, row in live.items() if row.get("action") == "steered")
    by_pr = {int(call["argv"][3].split("#", 1)[1].split(" ", 1)[0]): call for call in calls}
    run.step("session conflicts: each conflicting pull request gets exactly one claude -p --cloud message to its own unit's session, and the unit card records the head",
             steered == [21, 22, 23, 27, 32, 45] and len(calls) == 7 and sorted(by_pr) == [21, 22, 23, 27, 32, 45, 46]
             and all(call["argv"][:2] == ["-p", "--cloud"] and call["argv"][2].endswith(SESSION) for n, call in by_pr.items() if n != 46)
             and board.mutations() == mutations + 7 and all(f"Conflict follow-up: head {sha(n)} " in board.body(key(unit)) for n, unit, _, _ in cases if n in (21, 22, 23, 27, 46) and unit),
             json.dumps(steered))
    text = by_pr[21]["argv"][3]
    run.step("session conflicts: the message names the pull request and base, says merge (never rebase or force-push), resolve, rerun the checks, push and open nothing new, with no pull request text",
             text == ("PR #21 conflicts with main. Merge origin/main into your existing branch (a merge commit, never a rebase or force-push, unless the project rule in AGENTS.md says rebase), "
                      "resolve the conflicts, rerun the checks you ran and push. Do not open a new pull request or branch.") and "Change for" not in text, text[:160])
    run.step("session conflicts: a pull request GitHub reports unknown is re-polled in the same pass and steered when the second read says it conflicts",
             live[45]["action"] == "steered" and reads_of(45, gh_lines()[before_gh:]) == 2 and run.sleeps[sleeps:] == [5.0, 5.0], json.dumps(live[45]))
    run.step("session conflicts: a failed delivery is reported for the owner, once recorded so it is not tried again, and is the only one that failed",
             live[46]["action"] == "tell-owner" and live[46]["line"] == f"PR #46 conflicts with main and the follow-up was not delivered, head {sha(46)}"
             and f"Conflict follow-up: head {sha(46)} " in board.body(key("delivery-fails")), json.dumps(live[46]))
    run.step("session conflicts: the owner lines are the same as in the dry run and nothing was sent for them",
             {n: live[n].get("line") for n in (33, 35, 36)} == {n: rows[n]["line"] for n in (33, 35, 36)}, "")

    mutations, before_claude = board.mutations(), claude_calls(run)
    again = pass_over()
    run.step("session conflicts: a second run, a second tick or a webhook storm sends nothing and edits nothing, and a head with a follow-up reads as none",
             claude_calls(run) == before_claude and board.mutations() == mutations and again[21]["decision"] == "none" and again[46]["decision"] == "none"
             and {n: again[n]["action"] for n in (33, 35, 36)} == {33: "tell-owner", 35: "tell-owner", 36: "tell-owner"}, json.dumps(again[21]))
    refreshed = project("loop", "unit", "conflict-pending-ci", "--title", "Visitors see change 21")
    kept = pass_over()
    run.step("session conflicts: a later refresh of the unit card keeps the follow-up record, so the same head is not asked again",
             refreshed[0] == 0 and f"Conflict follow-up: head {sha(21)} " in board.body(key("conflict-pending-ci")) and claude_calls(run) == before_claude and kept[21]["decision"] == "none",
             board.body(key("conflict-pending-ci"))[-300:].replace("\n", " "))

    set_pull(21, head=sha(21, 1), mergeable=True)
    clean = pass_over()
    run.step("session conflicts: after the fix is pushed the head moves, the conflict clears, and nothing is sent",
             clean[21]["decision"] == "none" and claude_calls(run) == before_claude, json.dumps(clean[21]))
    set_pull(21, head=sha(21, 2), mergeable=False)
    second = pass_over()
    run.step("session conflicts: a new head that conflicts again (the base moved again) gets one more follow-up",
             second[21]["action"] == "steered" and claude_calls(run) == before_claude + 1 and sent()[-1]["argv"][2].endswith(SESSION), json.dumps(second[21]))
    set_pull(21, head=sha(21, 3))
    third = pass_over()
    run.step("session conflicts: a third conflicting head goes to the owner and nothing is sent", third[21]["action"] == "tell-owner" and "after 2 follow-ups" in third[21]["line"]
             and claude_calls(run) == before_claude + 1, json.dumps(third[21]))
    run.step("session conflicts: no run above merged, cancelled or re-ran CI, opened a session, or wrote to GitHub",
             len(writes(run)) == before_writes and not any("claude.ai" in json.dumps(call.get("argv")) and not call.get("steer") for call in sent()), "")
