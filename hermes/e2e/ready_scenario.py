from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, PROJECT, Board, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

FOOTER = "---- faker-kit machine fields (read by the kit, do not edit) ----"
SUMMARY = "Closing summary:\nDone: the change.\nSkipped: nothing.\nOpen questions: none.\nBlockers: none."
DRAFT_SENTENCE = ("is a draft, which is a hold: the kit marks a finished unit PR ready on the babysit tick; "
                  "a draft that is still running or red stays one; the owner marks it ready or asks for the merge")
BOOTSTRAP = "ong-xay"
POSTED = "2026-10-01T01:00:00Z"
MINUTE = 60


def unit_body(unit: str, pr: int) -> str:
    lines = [f"Key: faker-kit:{PROJECT}:{unit}", "Kind: unit", f"Project: {PROJECT}", f"Bot: {BOT}", "Outcome: merged-pr", "State: in-progress", f"PRs: {pr}", "Added: 1.0", "Updated: 1.0"]
    return "\n".join([f"Unit {unit}", "", FOOTER, *lines])


def ready_scenario(run: Run, check_run: Callable[..., dict], workflow_run: Callable[..., dict], ci_path: str) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)
    assert run.kit("loop", "migrate", "--apply", bot=CHIEF)[0] == 0
    assert run.kit("loop", "migrate", "--apply", bot=BOT)[0] == 0
    author = run.pull(0, "")["author"]

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def unit_pull(number: int, unit: str, draft: bool = True, summary: bool = True, green: bool = True, card: str | None = "ready", bound: int | None = None,
                  labeled: bool = True, body: str | None = None, state: str = "open") -> None:
        head = head_of(number)
        suite = (200 + number) * 100

        def change(gh: dict) -> None:
            gh["pulls"].append({**run.pull(number, head), "draft": draft, "state": state, "body": body if body is not None else f"Unit: {unit}\n\nChange for {unit}",
                                **({} if labeled else {"labels": []})})
            gh["check_runs"][head] = [check_run("check", suite, "success" if green else "failure")]
            gh["runs"][str(200 + number)] = workflow_run(head, path=ci_path, pulls=((number, "main"),), run_id=200 + number, branch=f"dispatch/unit-{number}")
            gh["pr_comments"][str(number)] = [{"user": {"login": author}, "body": SUMMARY, "created_at": POSTED}] if summary else []
        run.update_gh(change)
        if card is not None:
            board.add_card(key(unit), f"FK-{number} {unit}", unit_body(unit, bound or number), status="blocked" if card == "blocked" else "ready")
            if card == "running":
                board.leave_claim(key(unit), url=f"https://github.com/acme/shop/pull/{number}", head=head, commit=head)

    run.update_gh(lambda gh: gh.setdefault("pr_comments", {}))
    unit_pull(21, "unit-a", card="running")
    unit_pull(22, "unit-b", summary=False, card="running")
    unit_pull(23, "unit-c", green=False)
    unit_pull(24, "unit-d", card="blocked")
    unit_pull(25, "unit-e", card=None, body="a draft the owner opened by hand")
    unit_pull(26, "unit-f", draft=False)
    unit_pull(27, "unit-g", state="closed")
    unit_pull(32, "unit-i", bound=99)
    unit_pull(33, "unit-j", labeled=False)

    def pulls() -> dict[int, dict]:
        return {p["number"]: p for p in run.gh_now()["pulls"]}

    def graphql() -> int:
        return sum(1 for line in run.gh_log.read_text().splitlines() if json.loads(line)[:2] == ["api", "graphql"])

    def drafts() -> dict[int, bool]:
        return {number: bool(p.get("draft")) for number, p in pulls().items()}

    def reasons(out: dict) -> dict[int, str]:
        return {row["pr"]: row["reason"] for row in out["held"]}

    chief = run.kit("loop", "ready", bot=CHIEF)
    run.step("ready: loop ready is a project bot's action; the chief of staff is refused and no pull request changes",
             chief[0] == 1 and "not one of" in chief[2] and graphql() == 0, chief[2].strip())
    setup = run.kit("loop", "ready", bot=BOOTSTRAP)
    run.step("ready: the bootstrap bot, which has no loop command, is refused and no pull request changes",
             setup[0] == 1 and "not one of its commands" in setup[2] and graphql() == 0, setup[2].strip())
    arg = project("loop", "ready", "3")
    run.step("ready: loop ready takes no part id or other arguments", arg[0] == 1 and "takes no arguments" in arg[2], arg[2].strip())

    before = drafts()
    dry = json_of(project("loop", "ready", "--dry-run"))
    held = reasons(dry)
    run.step("ready dry run: it names the one draft it would mark and the reason each other draft stays, and marks nothing",
             dry.get("result") == "would mark" and [r["pr"] for r in dry["would_mark"]] == [21] and drafts() == before and graphql() == 0 and dry["marked"] == [] and dry["lines"] == []
             and "still running" in held[22] and "checks have not all passed (1 failed, 0 pending)" in held[23] and "waits on the owner" in held[24]
             and "not a unit pull request" in held[25] and "not bound to this pull request" in held[32] and "not labeled faker-kit" in held[33] and sorted(held) == [22, 23, 24, 25, 32, 33],
             json.dumps(held)[:400])

    first = project("loop", "ready")
    done = json_of(first)
    after = drafts()
    run.step("ready: the finished unit draft with green checks, a bound card and a closing summary is marked ready through GitHub's GraphQL call, one call, and prints its line",
             done.get("result") == "marked" and [r["pr"] for r in done["marked"]] == [21] and graphql() == 1 and run.gh_now()["ready_calls"] == [21]
             and done["lines"] == [f"marked #21 ready for review (unit unit-a, head {head_of(21)[:12]})"] and after[21] is False and "held" not in done, first[1].strip()[:300])
    run.step("ready: every other pull request keeps its state: the running draft, the red draft, the draft waiting on the owner, the hand-opened draft, the unbound and the unlabeled drafts, "
             "the closed draft and the ready pull request",
             {n: v for n, v in after.items() if n != 21} == {n: v for n, v in before.items() if n != 21} and pulls()[27]["state"] == "closed" and pulls()[27]["draft"] is True
             and pulls()[26]["draft"] is False and run.gh_now()["merges"] == [], json.dumps(after))
    calls = graphql()
    again = project("loop", "ready")
    twice = json_of(again)
    run.step("ready: a second run changes nothing and prints unchanged", twice.get("result") == "unchanged" and twice["marked"] == [] and twice["lines"] == [] and graphql() == calls
             and drafts() == after, again[1].strip()[:200])

    unit_pull(28, "unit-h", card="ready")
    run.update_gh(lambda gh: next(p for p in gh["pulls"] if p["number"] == 28).update(race_after_list=head_of(98)))
    moved = json_of(project("loop", "ready"))
    run.step("ready: a head that moves between the read and the write is not marked ready",
             moved.get("result") == "unchanged" and moved["marked"] == [] and graphql() == calls and pulls()[28]["draft"] is True and pulls()[28]["head"] == head_of(98), json.dumps(moved)[:300])
    run.update_gh(lambda gh: next(p for p in gh["pulls"] if p["number"] == 28).update(head=head_of(28)))

    run.update_gh(lambda gh: gh.update(ready_fail=True))
    refused = json_of(project("loop", "ready"))
    run.step("ready: a GitHub refusal of the write is reported as failed with its reason, marks nothing, and does not stop the pass",
             refused["marked"] == [] and [row["pr"] for row in refused["failed"]] == [28] and "403" in refused["failed"][0]["reason"] and pulls()[28]["draft"] is True, json.dumps(refused)[:300])
    run.update_gh(lambda gh: gh.update(ready_fail=False))
    calls = graphql()

    raised = project("andon", "raise", "bad", "merge", "landed")
    cord = json_of(project("loop", "ready", "--dry-run"))
    cleared = project("andon", "clear")
    run.step("ready: with the andon cord pulled no draft is marked, and the dry run says so",
             raised[0] == 0 and cord["would_mark"] == [] and all("andon" in row["reason"].lower() for row in cord["held"] if row["pr"] == 28) and cleared[0] == 0 and graphql() == calls,
             json.dumps(cord)[:300])

    unit_pull(29, "unit-m", summary=False, card=None)
    checks_read = len([line for line in run.gh_log.read_text().splitlines() if "check-runs" in line])
    merges = len(run.merged())
    plain = project("merge", "--pr", "29")
    waited = project("merge", "--pr", "29", "--wait", "120")
    run.step("merge: without --owner-approved a draft is refused with the one-sentence hold, before any check is read or waited for, with no GitHub 405 text, and is never marked ready",
             plain[0] != 0 and f"#29 {DRAFT_SENTENCE}" in plain[2] and waited[0] != 0 and f"#29 {DRAFT_SENTENCE}" in waited[2] and "405" not in plain[2] + waited[2]
             and len([line for line in run.gh_log.read_text().splitlines() if "check-runs" in line]) == checks_read and graphql() == calls and pulls()[29]["draft"] is True
             and len(run.merged()) == merges, (plain[2] + waited[2]).strip()[:300])
    approved = project("merge", "--pr", "29", "--owner-approved")
    result = json_of(approved)
    run.step("merge: with --owner-approved the same draft is marked ready through the same call and then merged on its exact head, and the result says marked_ready: true",
             approved[0] == 0 and result.get("merged") is True and result.get("marked_ready") is True and result["head"] == head_of(29) and pulls()[29]["state"] == "closed"
             and graphql() == calls + 1 and run.merged()[-1]["sha"] == head_of(29), approved[1].strip()[:300] or approved[2].strip())
    plain_ready = json_of(project("merge", "--pr", "26", "--owner-approved"))
    run.step("merge: a pull request that is not a draft merges as it always did, with no marked_ready and no ready call",
             plain_ready.get("merged") is True and "marked_ready" not in plain_ready and graphql() == calls + 1, json.dumps(plain_ready)[:300])

    unit_pull(30, "unit-k", draft=False)
    listing = project("merge", "--open", "--owner-approved")
    first_line = json.loads(listing[1].splitlines()[0]) if listing[1].strip() else {}
    run.step("merge: --open still lists every draft under skipped_drafts, marks none of them ready, and merges only the pull requests that were not drafts",
             listing[0] == 0 and sorted(row["number"] for row in first_line.get("skipped_drafts", [])) == [22, 23, 24, 25, 28, 32, 33] and {21, 30} <= {row["number"] for row in first_line["resolved"]}
             and not {22, 23, 24, 25, 28, 32, 33} & {row["number"] for row in first_line["resolved"]} and graphql() == calls + 1 and drafts()[22] is True,
             listing[1].splitlines()[0][:300] if listing[1].strip() else listing[2].strip())

    check_scenario(run, project, unit_pull)


def check_scenario(run: Run, project: Callable[..., tuple[int, str, str]], unit_pull: Callable[..., None]) -> None:
    def found(number: int) -> list[str]:
        report = json.loads(project("loop", "check")[1])
        return [f["line"] for f in report["findings"] if f["class"] == "draft_after_session" and f"PR #{number} " in f["line"]]

    def say(number: int, seconds_ago: float) -> None:
        stamp = datetime.fromtimestamp(run.clock - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        run.update_gh(lambda gh: gh["pr_comments"].__setitem__(str(number), [{"user": {"login": run.pull(0, "")["author"]}, "body": SUMMARY, "created_at": stamp}]))

    unit_pull(41, "unit-n", card="running", summary=False)
    say(41, 10 * MINUTE)
    early = found(41)
    run.clock += 25 * MINUTE
    late = found(41)
    run.step("loop check: a draft 10 minutes after its closing summary is not a finding, and 35 minutes after it is one that names the pull request, the fix and the owner's way out",
             early == [] and len(late) == 1 and "PR #41 is still a draft 35m after its session finished" in late[0] and "faker-kit loop ready" in late[0] and "reads the closing summary" in late[0],
             json.dumps(late)[:300])
    unit_pull(42, "unit-o", summary=False, card="running")
    run.clock += 60 * MINUTE
    run.step("loop check: a draft whose session is still running, with no closing summary, is never a finding", found(42) == [], json.dumps(found(42)))
    run.update_gh(lambda gh: next(p for p in gh["pulls"] if p["number"] == 41).update(draft=False))
    run.step("loop check: once the pull request is ready the finding is gone", found(41) == [], "")
