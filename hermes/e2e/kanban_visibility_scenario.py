from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, PROJECT, Board, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

HOUR = 3600
INSTALLED_LINE = "installed shop 2.4.1 on emulator-5554, sha256 " + "b" * 64
BUILT_LINE = "built release.tgz 2.4.1 for the store"
OLD_REFUSAL = "evidence is a JSON object for"


def part_body(ident: int, state: str, outcome: str = "merged-pr", prs: str = "") -> str:
    lines = [f"Key: faker-kit:chief:{ident}", "Kind: part", f"Project: {PROJECT}", f"Bot: {BOT}", f"Outcome: {outcome}", f"State: {state}",
             *([f"PRs: {prs}"] if prs else []), "Added: 1.0", "Updated: 1.0"]
    return "\n".join(["Part from the fixture", "", "---- faker-kit machine fields (read by the kit, do not edit) ----", *lines])


def unit_body(unit: str, session: bool = False) -> str:
    lines = [f"Key: faker-kit:{PROJECT}:{unit}", "Kind: unit", f"Project: {PROJECT}", f"Bot: {BOT}", "Outcome: merged-pr", "State: in-progress",
             "Added: 1.0", "Updated: 1.0", *(["Session: session_0123 launched"] if session else [])]
    return "\n".join([f"Unit {unit}", "", "---- faker-kit machine fields (read by the kit, do not edit) ----", *lines])


def visibility_scenario(run: Run) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def card(key: str) -> dict:
        return board.by_key()[key]

    def live_runs(key: str) -> list[dict]:
        ident = card(key)["id"]
        return [r for r in board.state()["runs"] if r["task_id"] == ident and r["ended_at"] is None]

    def problems(result: tuple[int, str, str]) -> list[str]:
        return [p["line"] for p in json_of(result).get("problems", [])]

    pre = project("loop", "check")
    run.step("visibility: before the cutover loop check is quiet and reads no Kanban", pre[0] == 0 and problems(pre) == [] and json_of(pre).get("cutover") is False, pre[1].strip())
    pre_launch = project("launch", "--brief-file", str(run.brief("before-cutover.md", "/faker-mode\nwork before the cutover\n")))
    run.step("visibility: before the cutover a launch with no unit card starts as it always did", pre_launch[0] == 0 and "card" not in json_of(pre_launch), pre_launch[2].strip())

    assert chief("loop", "migrate", "--apply")[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0
    quiet_before = problems(project("loop", "check"))

    mockup = board.add_card(f"faker-kit:{PROJECT}:kanban-visual-mockup", "kanban-visual-mockup", unit_body("kanban-visual-mockup"))
    run.update_gh(lambda st: st["pulls"].append({**run.pull(153, head_of(153)), "draft": True, "body": "Unit: kanban-visual-mockup\n\nmockup"}))
    case1 = project("loop", "check")
    named = [line for line in problems(case1) if mockup in line]
    run.step("visibility case 1: a ready unit card with no run while its draft PR #153 is open is named by loop check with the card, what it saw, and the fix",
             case1[0] == 0 and len(named) == 1 and "unit kanban-visual-mockup: PR #153 draft open, card ready, no run; fix: faker-kit loop reconcile" in named[0], json.dumps(named))
    fixed = project("loop", "reconcile")
    claims = json_of(fixed).get("claims", [])
    run.step("visibility case 1: loop reconcile claims the live unit's card, so the board shows it running with a run id, and loop check is quiet for it",
             fixed[0] == 0 and card(f"faker-kit:{PROJECT}:kanban-visual-mockup")["status"] == "running" and len(live_runs(f"faker-kit:{PROJECT}:kanban-visual-mockup")) == 1
             and not [line for line in problems(project("loop", "check")) if mockup in line], json.dumps(claims))

    late = run.brief("kanban-compact-board.md", "/faker-mode\nbuild the compact board\n")
    refused = project("launch", "--brief-file", str(late))
    run.step("visibility case 4: after the cutover a launch with no unit card refuses, names the unit and the exact command, and starts nothing",
             refused[0] != 0 and "no unit card for kanban-compact-board in shop" in refused[2] and "faker-kit loop unit kanban-compact-board --part <id>" in refused[2]
             and f"faker-kit:{PROJECT}:kanban-compact-board" not in board.by_key(), refused[2].strip())
    assert project("loop", "unit", "kanban-compact-board")[0] == 0
    launched = project("launch", "--brief-file", str(late))
    key = f"faker-kit:{PROJECT}:kanban-compact-board"
    shown = json_of(launched).get("card", {})
    session = live_runs(key)[0].get("session") if live_runs(key) else None
    run.step("visibility case 1 and 4: after loop unit the launch claims the card in the same call: running, one run with a 6 hour claim and the launch's session label",
             launched[0] == 0 and shown.get("claim") == "claimed" and card(key)["status"] == "running" and len(live_runs(key)) == 1
             and live_runs(key)[0]["ttl"] == 6 * HOUR and str(session).startswith("unit kanban-compact-board launch "), json.dumps(shown))

    run.clock += 5 * HOUR
    near = problems(project("loop", "check"))
    run.step("visibility lifecycle: a running unit card whose claim has under two hours left is reported by loop check with the reconcile command",
             any(card(key)["id"] in line and "expires in 60m" in line and "fix: faker-kit loop reconcile" in line for line in near), json.dumps(near))
    project("loop", "reconcile")
    run.clock += 5 * HOUR
    project("loop", "check")
    run.step("visibility lifecycle: the reconcile pass heartbeats the open unit, so the claim outlives its first 6 hour TTL on the same run",
             card(key)["status"] == "running" and [r["id"] for r in live_runs(key)] == [shown.get("run_id")]
             and any(h["task"] == card(key)["id"] for h in board.state().get("heartbeats", [])), json.dumps(board.state().get("heartbeats", [])))
    run.clock += 2 * HOUR
    stale = problems(project("loop", "check"))
    run.step("visibility lifecycle: a claim no pass kept alive goes stale in Kanban and loop check reports the card with the fix, never silently",
             any(card(key)["id"] in line and "went stale with no heartbeat, card ready, no run; fix: faker-kit loop reconcile" in line for line in stale), json.dumps(stale))
    project("loop", "reconcile")
    run.step("visibility lifecycle: the reconcile named in that line claims the card again on a new run",
             card(key)["status"] == "running" and len(live_runs(key)) == 1 and live_runs(key)[0]["id"] != shown.get("run_id"), json.dumps(live_runs(key)))
    closed = project("loop", "close", "kanban-compact-board", "--reason", "fixture ends")
    run.step("visibility lifecycle: closing a unit releases its live claim before the card is archived",
             closed[0] == 0 and live_runs(key) == [] and card(key)["status"] == "archived", closed[1].strip())

    ready_ids = [board.add_card(f"faker-kit:chief:{100 + i}", f"part {100 + i}", part_body(100 + i, "in-progress"), assignee=CHIEF) for i in range(24)]
    todo_ids = [board.add_card(f"faker-kit:chief:{200 + i}", f"part {200 + i}", part_body(200 + i, "in-progress"), assignee=CHIEF, status="todo") for i in range(5)]
    unit_for = [board.add_card(f"faker-kit:{PROJECT}:todo-unit-{i}", f"todo-unit-{i}", unit_body(f"todo-unit-{i}")) for i in range(5)]
    data = board.state()
    data["links"] += [[u, p] for u, p in zip(unit_for, todo_ids, strict=True)]
    board.path.write_text(json.dumps(data))
    listed = problems(chief("loop", "check"))
    run.step("visibility case 2: loop check lists all 24 ready and 5 todo part cards whose ledger says in-progress, each with its fix",
             all(any(i in line and "ledger in-progress" in line and "faker-kit loop align --apply" in line for line in listed) for i in ready_ids)
             and all(any(i in line and "none of its unit cards" in line for line in listed) for i in todo_ids), f"{len(listed)} lines")
    mutations = board.mutations()
    plan = json_of(chief("loop", "align", "--dry-run"))
    run.step("visibility case 2: the align dry run prints the plan for the 24 claimable cards and writes nothing",
             plan.get("count") == 24 and {r["card"] for r in plan["changes"]} == set(ready_ids) and board.mutations() == mutations, f"{plan.get('count')} planned")
    applied = json_of(chief("loop", "align", "--apply"))
    again = json_of(chief("loop", "align", "--apply"))
    run.step("visibility case 2: align --apply moves them to running, and a second apply changes nothing",
             applied.get("count") == 24 and all(card(f"faker-kit:chief:{100 + i}")["status"] == "running" for i in range(24)) and again.get("count") == 0, json.dumps(again))
    added = json_of(chief("handoff", "--project", PROJECT, "--part", "Ship the new flow", "--decisions", "owner said so"))
    moved = chief("loop", "set", str(added.get("id")), "--state", "in-progress")
    run.step("visibility case 2: loop set --state in-progress on a new part leaves its card running in the same call",
             moved[0] == 0 and json_of(moved).get("kanban", {}).get("claim") == "claimed" and card(f"faker-kit:chief:{added.get('id')}")["status"] == "running", moved[1].strip())
    waiting = chief("loop", "set", str(added.get("id")), "--state", "waiting", "--waits-on", "the owner")
    run.step("visibility case 2: loop set --state waiting releases the claim in the same call, so the card is ready again",
             waiting[0] == 0 and card(f"faker-kit:chief:{added.get('id')}")["status"] == "ready", waiting[1].strip())

    board.add_card("faker-kit:chief:163", "part 163", part_body(163, "in-progress", outcome="installed", prs="88"), assignee=CHIEF)
    run.update_gh(lambda st: st["pulls"].append(run.pull(88, head_of(88))))
    installed = chief("loop", "set", "163", "--evidence", f"installed={INSTALLED_LINE}")
    built = chief("loop", "set", "163", "--evidence", f"built={BUILT_LINE}")
    rows = {e["kind"]: e["payload"] for e in board.state()["evidence"] if e["task_id"] == card("faker-kit:chief:163")["id"]}
    run.step("visibility case 3: one-line installed= and built= evidence succeed and the command builds the app_installed and artifact_built payloads from facts it can read",
             installed[0] == 0 and built[0] == 0 and rows.get("app_installed") == {"text": INSTALLED_LINE, "head_sha": head_of(88), "sha256": "b" * 64, "version": "2.4.1", "device": "emulator-5554"}
             and rows.get("artifact_built") == {"text": BUILT_LINE, "head_sha": head_of(88), "version": "2.4.1"} and OLD_REFUSAL not in installed[2] + built[2], json.dumps(rows))
    by_hand = chief("loop", "set", "163", "--evidence", "built=" + json.dumps({"name": "release.tgz", "sha256": "a" * 64}))
    broken = chief("loop", "set", "163", "--evidence", 'built={"name": "x"}')
    run.step("visibility case 3: hand-written JSON still works, and a malformed one is refused naming the one-line form instead of only a shape",
             by_hand[0] == 0 and broken[0] != 0 and '--evidence built="<what was built, in one line of plain text>"' in broken[2], broken[2].strip())
    run.step("visibility case 3: the claim the evidence rode on is the part's own live claim, so recording evidence leaves the in-progress part running",
             card("faker-kit:chief:163")["status"] == "running", card("faker-kit:chief:163")["status"])

    run.step("visibility quiet: a board where nothing is wrong prints no problem line from loop check", quiet_before == [], json.dumps(quiet_before))
