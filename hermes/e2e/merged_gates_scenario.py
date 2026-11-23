from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, DECISIONS, FORBIDDEN_TOKENS, PROJECT, REPO, Board, commit_of, head_of, json_of, ledger, lines

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

REFUSALS = (("pending", "pending"), ("failure", "failure"), ("bare", "unknown"))
WRITES = ("claim", "edit", "create", "complete", "reclaim", "evidence", "archive", "block")
SESSION = "https://claude.ai/code/session_01E2Eabc"


def merged_gates_scenario(run: Run, open_gates: Callable[[Run, int, str], None]) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def owner(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=None)

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def card(unit: str) -> dict:
        return board.by_key()[key(unit)]

    def pull(n: int, unit: str | None, **fields: object) -> None:
        body = f"Unit: {unit}\n\nChange for {unit}" if unit else "A change with no unit line"
        run.update_gh(lambda state: state["pulls"].append({**run.pull(n, head_of(n)), "body": body, **fields}))

    def merged(n: int, unit: str | None) -> None:
        pull(n, unit, state="closed", merged_at="2026-10-10T00:00:00Z", merge_commit_sha=commit_of(n))

    def settle_on_github(n: int) -> None:
        run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == n).update(state="closed", merged_at="2026-10-10T00:00:00Z", merge_commit_sha=commit_of(n)))

    def unit_card(unit: str, n: int | None = None) -> None:
        extra = ["--pr", str(n)] if n else []
        assert project("loop", "unit", unit, "--title", "Visitors see the fixture change", *extra)[0] == 0

    def running_with_pr(unit: str, n: int, done: bool) -> None:
        (merged if done else pull)(n, unit)
        unit_card(unit, n)
        run_id = board.leave_claim(key(unit), url=f"https://github.com/{REPO}/pull/{n}", head=head_of(n), commit=commit_of(n))
        board.edit_card(key(unit), body=board.body(key(unit)).rstrip("\n") + f"\nRun: {run_id} session {SESSION}\nSession: {SESSION}")

    def pending_rows(out: dict) -> dict[str, dict]:
        return {r["id"]: r for r in out.get("entries", {}).get("completion_pending", [])}

    def project_refusals() -> dict:
        try:
            return json.loads((run.team / "refusals" / f"{BOT}.json").read_text())
        except (OSError, ValueError):
            return {}

    def sessions() -> int:
        return len(run.claude_log.read_text().splitlines()) if run.claude_log.exists() else 0

    assert run.kit("loop", "migrate", "--apply", bot=CHIEF)[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0

    merged(110, "landed")
    unit_card("landed", 110)
    seen_classes: list[str] = []
    for mode, wanted in REFUSALS:
        board.set(complete_refuse=mode)
        run.clock += 7 * 3600
        out = json_of(project("loop", "reconcile"))
        row = pending_rows(out).get("landed", {})
        seen_classes.append(str(row.get("classification")))
        run.step(f"merged gates 1: Kanban refusing completion ({mode}) leaves the card header State merged with the pull request number, the claim released, the card scheduled and not archived, "
                 "and reports a pending row, not a refusal",
                 "State: merged" in lines(card("landed")) and "PRs: 110" in lines(card("landed")) and board.open_claims(key("landed")) == [] and card("landed")["status"] == "scheduled"
                 and row.get("completion") == "pending" and row.get("classification") == wanted and row.get("detail") and out.get("completion_pending") == 1
                 and not [r for r in out.get("entries", {}).get("refused", []) if r["id"] == "landed"], json.dumps(row)[:240])
    run.step("merged gates 1: the refusal that names no receipt (Kanban's bare refusal) prints the missing receipt in its notice instead of an empty detail",
             "receipt: none for this run" in str(row.get("detail")), str(row.get("detail")))
    after_last, calls_last = board.mutations(), len(board.calls())
    again = json_of(project("loop", "reconcile"))
    run.step("merged gates 1: the next pass prints the same pending row as held and makes no Kanban write, because the header already says merged and nothing is claimed again",
             pending_rows(again).get("landed", {}).get("held") is True and board.mutations() == after_last and len(board.calls()) > calls_last and card("landed")["status"] == "scheduled",
             json.dumps(pending_rows(again))[:240])
    board.set(complete_refuse=None)
    run.clock += 7 * 3600
    done = json_of(project("loop", "reconcile"))
    tokens = {token for _, rest in board.calls() for token in rest}
    run.step("merged gates 1: once Kanban accepts, the retry on the hold completes the card by itself, the header still says merged, and no forced completion, local completion, archive or "
             "evidence the checks did not produce ever happened",
             done.get("entries", {}).get("merged") == ["landed"] and card("landed")["status"] == "done" and "State: merged" in lines(card("landed")) and not tokens & FORBIDDEN_TOKENS
             and all(c["kinds"] == ["pr_merged", "pr_published"] for c in board.state()["completions"]) and len([c for c in board.state()["completions"] if c["task"] == card("landed")["id"]]) == 4,
             json.dumps(done.get("entries")))

    def completed(unit: str) -> list[dict]:
        return [c for c in board.state()["completions"] if c["task"] == card(unit)["id"]]

    def card_runs(unit: str) -> list[dict]:
        return [r for r in board.state()["runs"] if r["task_id"] == card(unit)["id"]]

    def card_evidence(unit: str) -> list[dict]:
        return [e for e in board.state()["evidence"] if e["task_id"] == card(unit)["id"]]

    def reconcile() -> tuple[dict, int]:
        mark = len(board.calls())
        run.clock += 7 * 3600
        return json_of(project("loop", "reconcile")), mark

    merged(111, "finished")
    unit_card("finished", 111)
    board.set(done_before_claim=[card("finished")["id"]], merged_bound=[card("finished")["id"]])
    settled, mark = reconcile()
    after_done, calls_done = board.mutations(), len(board.calls())
    reconcile()
    run.step("merged gates 1: Kanban answering the claim of a done card with a bound merged pull request with exit 0 and already_done true and a null run id is a success: the card ends State "
             "merged with the pull request number, it is listed as merged, not as completion_pending or refused, no run is opened, no evidence is written, "
             "nothing is completed or reclaimed, the refusal hold keeps "
             "no entry for it, "
             "and the next pass makes no Kanban write",
             "finished" in settled.get("entries", {}).get("merged", []) and "State: merged" in lines(card("finished")) and "PRs: 111" in lines(card("finished"))
             and card("finished")["status"] == "done" and board.open_claims(key("finished")) == [] and "finished" not in pending_rows(settled)
             and settled.get("completion_pending", 0) == 0 and not [r for r in settled.get("entries", {}).get("refused", []) if r["id"] == "finished"]
             and card("finished")["id"] not in project_refusals() and board.mutations() == after_done
             and card_runs("finished") == [] and card_evidence("finished") == [] and completed("finished") == []
             and ("claim",) in board.verbs()[len(board.verbs()) - (len(board.calls()) - mark):]
             and not [v for v in board.verbs()[calls_done:] if v[0] in WRITES and v != ("evidence", "list")], json.dumps(settled.get("entries"))[:240])

    merged(112, "unbound")
    unit_card("unbound", 112)
    board.set(done_before_claim=[card("unbound")["id"]])
    unbound, _ = reconcile()
    row = pending_rows(unbound).get("unbound", {})
    run.step("merged gates 1: the claim refusal of a done card with no merged pull request bound (exit 1, `cannot claim ... no merged pull request is bound`) is a refusal, never a success: it is a "
             "pending row "
             "that keeps the text, no run, evidence or completion exists, and the card is not listed as merged",
             row.get("completion") == "pending" and "no merged pull request is bound" in str(row.get("detail")) and "unbound" not in unbound.get("entries", {}).get("merged", [])
             and card_runs("unbound") == [] and card_evidence("unbound") == [] and completed("unbound") == [], json.dumps(row)[:240])

    merged(113, "wording")
    unit_card("wording", 113)
    board.leave_claim(key("wording"), url=f"https://github.com/{REPO}/pull/113", head=head_of(113), commit=commit_of(113))
    board.set(complete_refuse="text-already done")
    wording, _ = reconcile()
    row = pending_rows(wording).get("wording", {})
    run.step("merged gates 1: a completion refusal whose text merely contains the words already done stays a refusal: a pending row with that "
             "text, the claim released, and the card not listed as merged",
             row.get("completion") == "pending" and "already done" in str(row.get("detail")) and "wording" not in wording.get("entries", {}).get("merged", [])
             and board.open_claims(key("wording")) == [] and card("wording")["status"] == "scheduled", json.dumps(row)[:240])
    board.set(complete_refuse=None)

    merged(114, "plain")
    unit_card("plain", 114)
    board.set(claim_extra={"already_done": False})
    plain, _ = reconcile()
    board.set(claim_extra={})
    run.step("merged gates 1: a claim answer with already_done false and a run id takes the normal path: one run, the pull request evidence, one completion, and the card done",
             "plain" in plain.get("entries", {}).get("merged", []) and card("plain")["status"] == "done" and len(card_runs("plain")) == 1 and len(completed("plain")) == 1
             and sorted(e["kind"] for e in card_evidence("plain")) == ["pr_merged", "pr_published"] and "plain" not in pending_rows(plain), json.dumps(plain.get("entries"))[:240])

    owner("pickup", "on")
    guard = run.team / "guard.yaml"
    guard.write_text(re.sub(r"^max_in_flight_per_project:.*\n", "", guard.read_text().replace("max_in_flight_total: 500", "max_in_flight_total: 10"), flags=re.M))
    run.update_gh(lambda state: state["files"].__setitem__(".agents/dispatch.json", json.dumps({"max_in_flight": 1})))
    board.set(tasks=[{**t, "status": "done" if t["status"] == "running" else t["status"]} for t in board.state()["tasks"]])
    slot = [f"slot-{i}" for i in range(10)]
    for index, unit in enumerate(slot):
        running_with_pr(unit, 200 + index, True)
    running_with_pr("slot-open", 210, False)
    board.set(complete_refuse="pending")
    before = json_of(project("pickup", "status", "--no-job"))
    naive = [t for t in board.state()["tasks"] if t["status"] == "running" and f"Project: {PROJECT}" in t["body"] and "Kind: unit" in t["body"]]
    unit_card("fresh-unit")
    brief = run.brief("fresh-unit.md", "/faker-mode\nbuild it\n")
    run.step("merged gates 2, before the settle: 11 unit cards are running (10 of them already merged on GitHub), which is what a count of Kanban's running status sees; the cap is full",
             len(naive) == 11 and before.get("running") == 11 and before.get("running_total") == 11 and before.get("free_slots") == 0,
             json.dumps({k: before.get(k) for k in ("running", "running_total", "free_slots")}))
    json_of(project("loop", "reconcile"))
    after = json_of(project("pickup", "status", "--no-job"))
    sent = sessions()
    full = project("launch", "--brief-file", str(brief))
    run.step("merged gates 2: after the settle the 10 merged cards hold no slot whatever their Kanban status says, so pickup status counts 1 (the card with the open pull request) of 1 per project "
             "and 10 in total, and a launch is still refused naming that card (the cap still works), claiming and starting nothing",
             after.get("running") == 1 and after.get("running_total") == 1 and after.get("per_project") == 1 and after.get("total") == 10 and after.get("free_slots") == 0
             and full[0] != 0 and "1 of 1 unit cards" in full[2] and f"{card('slot-open')['id']} slot-open" in full[2] and "slot-0" not in full[2] and sessions() == sent
             and card("fresh-unit")["status"] == "ready", full[2].strip())
    settle_on_github(210)
    json_of(project("loop", "reconcile"))
    freed = json_of(project("pickup", "status", "--no-job"))
    launched = project("launch", "--brief-file", str(brief))
    run.step("merged gates 2: once the last pull request merges its card frees the slot too, and a launch of a new unit is accepted at 1 per project and 10 in total and claims its card",
             freed.get("running") == 0 and freed.get("free_slots") == 1 and launched[0] == 0 and json_of(launched).get("card", {}).get("claim") == "claimed"
             and card("fresh-unit")["status"] == "running" and sessions() == sent + 1, launched[2].strip() or launched[1][:160])
    board.edit_card(key("fresh-unit"), status="done")
    unit_card("live-unit")
    board.set(complete_refuse=None)
    live = project("launch", "--brief-file", str(run.brief("live-unit.md", "/faker-mode\nbuild the live one\n")))
    unit_card("second-unit")
    capped = project("launch", "--brief-file", str(run.brief("second-unit.md", "/faker-mode\nbuild the second one\n")))
    run.step("merged gates 2: a card with a live session still holds its slot", live[0] == 0 and capped[0] != 0 and "1 of 1 unit cards" in capped[2] and "live-unit" in capped[2], capped[2].strip())
    board.edit_card(key("live-unit"), status="done")

    made = json_of(run.kit("handoff", "--project", PROJECT, "--part", "Build the nightly archive", "--title", "Nightly archive builds", "--decisions", DECISIONS, "--outcome", "built", bot=CHIEF))
    assert project("loop", "unit", "built-unit", "--title", "Nightly archive builds", "--part", str(made["id"]), "--pr", "400")[0] == 0
    merged(400, "built-unit")
    board.leave_claim(key("built-unit"), url=f"https://github.com/{REPO}/pull/400", head=head_of(400), commit=commit_of(400))
    held = json_of(project("pickup", "status", "--no-job"))
    waiting = json_of(project("loop", "reconcile"))
    free = json_of(project("pickup", "status", "--no-job"))
    run.step("merged gates 5: a unit whose merged pull request still waits for its stage evidence (built) is closed in the kit and holds no slot, and the stage evidence rule is unchanged",
             held.get("running") == 1 and free.get("running") == 0 and free.get("free_slots") == 1 and {"id": "built-unit", "outcome": "built"} in waiting["entries"]["awaiting_stage_evidence"]
             and "State: merged" in lines(card("built-unit")) and board.open_claims(key("built-unit")) == [] and card("built-unit")["status"] == "scheduled",
             json.dumps({"before": held.get("running"), "after": free.get("running")}))

    def ready(n: int) -> None:
        open_gates(run, n, head_of(n))

    for n, unit in ((301, None), (302, None), (303, None), (304, None), (305, "bound-line"), (306, None), (307, None)):
        pull(n, unit)
    owner("pickup", "off")
    for unit, n in (("bound-header", 301), ("bound-evidence", None), ("bound-twice-a", 303), ("bound-twice-b", 303), ("bound-other-bot", 304), ("bound-line", 305), ("bound-elsewhere", 307)):
        unit_card(unit, n)
    board.edit_card(key("bound-other-bot"), assignee=CHIEF)
    board.add_evidence(key("bound-evidence"), "pr_published", {"pr_url": f"https://github.com/{REPO}/pull/302", "head_sha": head_of(302)}, run_id=1)
    board.add_evidence(key("bound-elsewhere"), "pr_published", {"pr_url": f"https://github.com/{REPO}/pull/399", "head_sha": head_of(399)}, run_id=1)
    status_line = [line for line in project("status")[1].splitlines() if line.startswith("#301 ")]
    refusals = {}
    for n in (303, 304, 306, 307):
        ready(n)
        refusals[n] = project("merge", "--pr", str(n))
    refused_303, refused_304, refused_306, refused_307 = (refusals[n] for n in (303, 304, 306, 307))
    run.step("merged gates 3: a pull request with no Unit line is refused as before when two cards record it, when the card belongs to another bot, when no card records it, and when the card "
             "is bound to another pull request",
             refused_303[0] == 1 and "unit cards bound-twice-a, bound-twice-b all record it" in refused_303[2] and refused_304[0] == 1 and "belongs to" in refused_304[2]
             and refused_306[0] == 1 and "no `Unit: <id>` first line" in refused_306[2] and refused_307[0] == 1 and "bound to another pull request" in refused_307[2]
             and not {303, 304, 306, 307} & {m["pr"] for m in run.merged()}, refused_303[2].strip())
    run.step("merged gates 3: the status line names the unit of a pull request whose body has no Unit line, from the card's PRs field", bool(status_line) and "unit bound-header" in status_line[0],
             " ".join(status_line))
    landed = {}
    for n in (301, 302, 305):
        ready(n)
        landed[n] = project("merge", "--pr", str(n))
    by_header, by_evidence, by_line = landed[301], landed[302], landed[305]
    run.step("merged gates 3: a pull request with no Unit line is found from the card whose PRs lists its number, or whose pr_published evidence names its url, and merges and completes the card; "
             "a Unit line stays the primary signal",
             by_header[0] == 0 and json_of(by_header).get("card", {}).get("completion") == "done" and by_evidence[0] == 0 and json_of(by_evidence).get("card", {}).get("completion") == "done"
             and by_line[0] == 0
             and {301, 302, 305} <= {m["pr"] for m in run.merged()} and card("bound-header")["status"] == "done" and card("bound-evidence")["status"] == "done",
             by_header[1].strip()[:200])
    linked = project("loop", "unit", "bound-new", "--title", "Visitors see the fixture change", "--pr", "308")
    shown = json_of(linked)
    again = json_of(project("loop", "unit", "bound-new", "--title", "Visitors see the fixture change", "--pr", "308"))
    run.step("merged gates 3: loop unit <id> --pr <n> records the pull request on the card and says that linked means only a part link, so linked false with no --part is not a failure",
             linked[0] == 0 and shown.get("prs") == [308] and shown.get("linked") is False and "no --part was given" in str(shown.get("linked_note")) and "PRs: 308" in lines(card("bound-new"))
             and again.get("prs") == [308], json.dumps(shown)[:300])

    unit_card("check-bad")
    unit_card("check-good")
    owner("pickup", "on")
    running_with_pr("check-busy", 330, False)
    marks, calls, mutations = sessions(), len(board.calls()), board.mutations()
    bad = project("launch", "--brief-file", str(run.brief("check-bad.md", "/faker-mode\nbuild it\n", decided=False)), "--check")
    report = json_of(bad)
    problems = " | ".join(report.get("problems", []))
    run.step("merged gates 4: launch --check on a brief with no OWNER DECISIONS and no CHANGES line, with every slot taken, prints all three refusals at once, exits 0 and writes nothing",
             bad[0] == 0 and report.get("ok") is False and report.get("started") is False and "OWNER DECISIONS" in problems and "CHANGES" in problems and "pickup limits" in problems
             and len(report.get("problems", [])) == 3 and sessions() == marks and board.mutations() == mutations and card("check-bad")["status"] == "ready"
             and not [v for v in board.verbs()[calls:] if v[0] in ("claim", "edit", "create", "complete")], bad[1].strip()[:240])
    board.edit_card(key("check-busy"), status="done")
    good = project("launch", "--brief-file", str(run.brief("check-good.md", "/faker-mode\nbuild it\n")), "--check")
    good_report = json_of(good)
    run.step("merged gates 4: launch --check on a good brief prints ok with no problems, and nothing started or claimed",
             good[0] == 0 and good_report == {"check": True, "ok": True, "problems": [], "started": False} and sessions() == marks and card("check-good")["status"] == "ready"
             and board.mutations() == mutations, good[1].strip()[:240])
    real = project("launch", "--brief-file", str(run.brief("check-good.md", "/faker-mode\nbuild it\n")))
    run.step("merged gates 4: the same brief then launches for real through the same code and claims its card, so the check consumed nothing",
             real[0] == 0 and json_of(real).get("card", {}).get("claim") == "claimed" and sessions() == marks + 1 and card("check-good")["status"] == "running", real[2].strip() or real[1][:160])
    board.edit_card(key("check-good"), status="done")
