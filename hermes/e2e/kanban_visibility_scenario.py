from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, PROJECT, Board, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

HOUR = 3600
TTL = 8 * HOUR
INSTALLED_LINE = "app_id=com.shop.app host=emulator-5554 artifact_sha256=" + "b" * 64 + " readback_sha256=" + "b" * 64
BUILT_LINE = "release-2.4.1.tgz " + "b" * 64
OLD_REFUSAL = "evidence is a JSON object for"
STALE_BLOCKED = ("t_13b1a9a1", "t_2046fbaa", "t_a42c3979", "t_2156551a", "t_4d4b9fb7")
FOOTER = "---- faker-kit machine fields (read by the kit, do not edit) ----"


def part_body(ident: int, state: str, outcome: str = "merged-pr", prs: str = "") -> str:
    lines = [f"Key: faker-kit:chief:{ident}", "Kind: part", f"Project: {PROJECT}", f"Bot: {BOT}", f"Outcome: {outcome}", f"State: {state}",
             *([f"PRs: {prs}"] if prs else []), "Added: 1.0", "Updated: 1.0"]
    return "\n".join(["Part from the fixture", "", FOOTER, *lines])


def unit_body(unit: str, part: int | None = None) -> str:
    lines = [f"Key: faker-kit:{PROJECT}:{unit}", "Kind: unit", f"Project: {PROJECT}", f"Bot: {BOT}", "Outcome: merged-pr", "State: in-progress",
             *([f"Part: {part}"] if part else []), "Added: 1.0", "Updated: 1.0"]
    return "\n".join([f"Unit {unit}", "", FOOTER, *lines])


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

    def findings(result: tuple[int, str, str], kind: str | None = None) -> list[dict]:
        found = json.loads(result[1]).get("findings", []) if result[1].strip() else []
        return [f for f in found if kind is None or f["class"] == kind]

    def lines_for(result: tuple[int, str, str], ident: str) -> list[str]:
        return [f["line"] for f in findings(result) if ident in f["line"]]

    def change(edit: Callable[[dict], object]) -> None:
        data = board.state()
        edit(data)
        board.path.write_text(json.dumps(data))

    pre = project("loop", "check")
    run.step("visibility: before the cutover loop check is quiet, exits 0, and reads no Kanban", pre[0] == 0 and findings(pre) == [] and json_of(pre).get("cutover") is False, pre[1].strip())
    pre_launch = project("launch", "--brief-file", str(run.brief("before-cutover.md", "/faker-mode\nwork before the cutover\n")))
    run.step("visibility: before the cutover a launch with no unit card starts as it always did", pre_launch[0] == 0 and "card" not in json_of(pre_launch), pre_launch[2].strip())

    assert chief("loop", "migrate", "--apply")[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0

    mockup_key = f"faker-kit:{PROJECT}:kanban-visual-mockup"
    mockup = board.add_card(mockup_key, "kanban-visual-mockup", unit_body("kanban-visual-mockup"))
    run.update_gh(lambda st: st["pulls"].append({**run.pull(153, head_of(153)), "draft": True, "body": "Unit: kanban-visual-mockup\n\nmockup"}))
    case1 = project("loop", "check")
    named = lines_for(case1, mockup)
    run.step("visibility case 1: a ready unit card with no run while its draft PR #153 is open is named by loop check (card, unit, what it saw, fix) and the check exits 4",
             case1[0] == 4 and named == [f"{mockup} unit kanban-visual-mockup: PR #153 draft open, card ready, no run; fix: faker-kit loop reconcile"]
             and "needs action" in case1[2], json.dumps(named))
    project("loop", "reconcile")
    run.step("visibility case 1: loop reconcile claims the live unit's card with the 8 hour TTL, so the board shows it running with a run id and loop check is quiet for it",
             card(mockup_key)["status"] == "running" and len(live_runs(mockup_key)) == 1 and live_runs(mockup_key)[0]["ttl"] == TTL
             and lines_for(project("loop", "check"), mockup) == [], json.dumps(live_runs(mockup_key)))

    late = run.brief("kanban-compact-board.md", "/faker-mode\nbuild the compact board\n")
    key = f"faker-kit:{PROJECT}:kanban-compact-board"
    refused = project("launch", "--brief-file", str(late))
    run.step("visibility case 4: after the cutover a launch with no unit card refuses, names the unit and the exact command, and starts nothing",
             refused[0] != 0 and "no unit card for kanban-compact-board in shop" in refused[2] and "faker-kit loop unit kanban-compact-board --part <id>" in refused[2]
             and key not in board.by_key(), refused[2].strip())
    assert project("loop", "unit", "kanban-compact-board", "--title", "Visitors see the fixture change")[0] == 0
    launched = project("launch", "--brief-file", str(late))
    out = json_of(launched)
    shown = out.get("card", {})
    session = str(out.get("session"))
    run.step("visibility case 1 and 4: after loop unit the launch claims the card itself: running, one run with ttl 28800 and the session id as --session, and the run id on the card",
             launched[0] == 0 and shown.get("claim") == "claimed" and card(key)["status"] == "running" and len(live_runs(key)) == 1
             and live_runs(key)[0]["ttl"] == TTL and live_runs(key)[0]["session"] == session and f"Run: {shown.get('run_id')} session {session}" in board.body(key), json.dumps(shown))
    twice = project("launch", "--brief-file", str(late))
    run.step("visibility case 1: a second launch of a running unit refuses and starts no second session",
             twice[0] != 0 and "already running" in twice[2] and len(live_runs(key)) == 1, twice[2].strip())

    run.clock += 7 * HOUR
    swept = project("loop", "check")
    run.step("visibility lifecycle: with an hour left the sweep in loop check heartbeats the live unit's claim back to 8 hours (heartbeat has no --ttl; the kit sets the TTL env), quietly",
             swept[0] == 0 and lines_for(swept, card(key)["id"]) == [] and live_runs(key)[0]["claim_expires"] == run.clock + TTL
             and [r["id"] for r in live_runs(key)] == [shown.get("run_id")], json.dumps(live_runs(key)))
    run.clock += TTL - 15 * 60
    late_sweep = project("loop", "check")
    run.step("visibility lifecycle: a claim within 30 minutes of expiry is listed with the extension the sweep made, and does not count as needing action",
             late_sweep[0] == 0 and any("was 15m from expiry; extended to 480m" in line for line in lines_for(late_sweep, card(key)["id"])), json.dumps(lines_for(late_sweep, card(key)["id"])))
    run.clock += TTL + 60
    stale = project("loop", "check")
    run.step("visibility lifecycle: a claim no pass kept alive goes stale in Kanban; loop check names the card as live with no run and exits 4, never silently",
             stale[0] == 4 and any(f"session {session} live, card ready, no run; fix: faker-kit loop reconcile" in line for line in lines_for(stale, card(key)["id"])),
             json.dumps(lines_for(stale, card(key)["id"])))
    project("loop", "reconcile")
    run.step("visibility lifecycle: the reconcile named in that line claims the card again on a new run",
             card(key)["status"] == "running" and len(live_runs(key)) == 1 and live_runs(key)[0]["id"] != shown.get("run_id"), json.dumps(live_runs(key)))
    closed = project("loop", "close", "kanban-compact-board", "--reason", "fixture ends")
    run.step("visibility lifecycle: closing a unit releases its live claim with reclaim --reason before the card is archived",
             closed[0] == 0 and live_runs(key) == [] and card(key)["status"] == "archived", closed[1].strip())

    pending = board.add_card(f"faker-kit:{PROJECT}:merged-pending", "merged-pending", unit_body("merged-pending"))

    def stuck(data: dict) -> None:
        for ident in STALE_BLOCKED:
            data["tasks"].append({"id": ident, "title": f"stuck {ident}", "body": unit_body(f"stuck-{ident[2:]}"), "assignee": BOT, "status": "blocked", "created_by": BOT,
                                  "completion_contract": "fleet-ci:acme/shop", "outcome_contract": json.dumps({"kind": "merged_pr", "repo": "acme/shop"}), "result": None, "idempotency": ident})
            data["events"] += [{"task_id": ident, "kind": "reclaimed", "run_id": 1, "created_at": 1, "payload": {"stale_lock": f"external:{BOT}:1"}},
                               {"task_id": ident, "kind": "reclaimed", "run_id": 2, "created_at": 2, "payload": {"stale_lock": f"external:{BOT}:2"}},
                               {"task_id": ident, "kind": "gave_up", "run_id": 2, "created_at": 3, "payload": {}}]
        data["events"].append({"task_id": pending, "kind": "pr_acceptance", "run_id": 9, "created_at": 4,
                               "payload": {"ok": False, "classification": "pending", "detail": "CI acceptance pending on the exact head"}})

    change(stuck)
    swept_board = project("loop", "check")
    blocked = findings(swept_board, "stale_blocked")
    merged = findings(swept_board, "merged_not_done")
    run.step("visibility stale: the five cards Kanban gave up on after two stale reclaims (t_13b1a9a1 and four more) are each listed with the unblock line the kit never runs",
             sorted(f["card"] for f in blocked) == sorted(STALE_BLOCKED) and all(f"hermes kanban unblock {f['card']}" in f["fix"] for f in blocked)
             and "unblock" not in [c[0] for c in board.calls()], json.dumps([f["line"] for f in blocked])[:300])
    run.step("visibility merged: a card whose merged pull request is not done is listed with the refusal phase from its last pr_acceptance event",
             [f["card"] for f in merged] == [pending] and "acceptance pending: CI acceptance pending on the exact head" in merged[0]["saw"], json.dumps(merged))

    running_units = [board.add_card(f"faker-kit:{PROJECT}:work-{i}", f"work-{i}", unit_body(f"work-{i}", 100 + i), status="running") for i in range(24)]
    ready_parts = [board.add_card(f"faker-kit:chief:{100 + i}", f"part {100 + i}", part_body(100 + i, "in-progress"), assignee=CHIEF) for i in range(24)]
    idle_units = [board.add_card(f"faker-kit:{PROJECT}:idle-{i}", f"idle-{i}", unit_body(f"idle-{i}", 200 + i)) for i in range(5)]
    todo_parts = [board.add_card(f"faker-kit:chief:{200 + i}", f"part {200 + i}", part_body(200 + i, "in-progress"), assignee=CHIEF, status="todo") for i in range(5)]
    done_unit = board.add_card(f"faker-kit:{PROJECT}:shipped", "shipped", unit_body("shipped", 300), status="done")
    done_part = board.add_card("faker-kit:chief:300", "part 300", part_body(300, "in-progress"), assignee=CHIEF)
    pairs = [*zip(running_units, ready_parts, strict=True), *zip(idle_units, todo_parts, strict=True), (done_unit, done_part)]
    change(lambda data: data["links"].extend([u, p] for u, p in pairs))
    parts = chief("loop", "check")
    flagged = {f["card"] for f in findings(parts, "part_inconsistent")}
    run.step("visibility case 2: a ready part card whose unit card runs is healthy and is not forced to running (Pal refuses a claim while prerequisites are open)",
             not flagged & set(ready_parts) and all(card(f"faker-kit:chief:{100 + i}")["status"] == "ready" for i in range(24)), f"{len(flagged)} flagged")
    run.step("visibility case 2: loop check flags the 5 todo parts in progress with no running unit, and the part whose units are all done, each with its fix, and exits 4",
             parts[0] == 4 and set(todo_parts) <= flagged and done_part in flagged
             and all("faker-kit loop reconcile" in f["fix"] for f in findings(parts, "part_inconsistent") if f["card"] in (*todo_parts, done_part)), json.dumps(sorted(flagged)))
    mutations = board.mutations()
    plan = json_of(chief("loop", "align", "--dry-run"))
    run.step("visibility case 2: the align dry run lists the Work text each part card would show and writes nothing",
             plan.get("dry_run") is True and plan.get("count", 0) >= 30 and board.mutations() == mutations
             and {"card": ready_parts[0], "part": "faker-kit:chief:100", "work": f"in progress: unit work-0 running (card {running_units[0]})"} in plan["changes"], f"{plan.get('count')} planned")
    applied = json_of(chief("loop", "align", "--apply"))
    again = json_of(chief("loop", "align", "--apply"))
    run.step("visibility case 2: align --apply writes the Status text on each part card, and a second apply changes nothing",
             applied.get("count") == plan.get("count") and again.get("count") == 0 and f"in progress: unit work-0 running (card {running_units[0]})" in board.body("faker-kit:chief:100")
             and "Work: every unit done" in board.body("faker-kit:chief:300"), json.dumps(again))
    added = json_of(chief("handoff", "--project", PROJECT, "--part", "Ship the new flow", "--title", "Ship the new flow", "--decisions", "owner said so"))
    part_key = f"faker-kit:chief:{added.get('id')}"
    moved = chief("loop", "set", str(added.get("id")), "--state", "in-progress")
    run.step("visibility case 2: loop set --state in-progress on a new part writes its Status text in the same call and leaves the Kanban status to Pal",
             moved[0] == 0 and "State: in-progress" in board.body(part_key) and "Work: no unit card yet" in board.body(part_key) and card(part_key)["status"] == "ready", moved[1].strip())

    board.add_card("faker-kit:chief:163", "part 163", part_body(163, "in-progress", outcome="installed", prs="88"), assignee=CHIEF)
    run.update_gh(lambda st: st["pulls"].append(run.pull(88, head_of(88))))
    installed = chief("loop", "set", "163", "--evidence", f"installed={INSTALLED_LINE}")
    built = chief("loop", "set", "163", "--evidence", f"built={BUILT_LINE}")
    rows = {e["kind"]: e["payload"] for e in board.state()["evidence"] if e["task_id"] == card("faker-kit:chief:163")["id"]}
    run.step("visibility case 3: one-line installed= and built= evidence build exactly the app_installed and artifact_built records Kanban's contract takes, and nothing else",
             installed[0] == 0 and built[0] == 0 and rows.get("app_installed") == {"app_id": "com.shop.app", "artifact_sha256": "b" * 64, "host": "emulator-5554", "readback_sha256": "b" * 64}
             and rows.get("artifact_built") == {"name": "release-2.4.1.tgz", "sha256": "b" * 64} and OLD_REFUSAL not in installed[2] + built[2], json.dumps(rows))
    by_hand = chief("loop", "set", "163", "--evidence", "built=" + json.dumps({"name": "release.tgz", "sha256": "a" * 64}))
    broken = chief("loop", "set", "163", "--evidence", 'built={"name": "x"}')
    run.step("visibility case 3: hand-written JSON still works, and a malformed one is refused naming the exact fields and both forms",
             by_hand[0] == 0 and broken[0] != 0 and "exactly these text fields: name, sha256" in broken[2] and '--evidence built="<what was built> <its sha256' in broken[2], broken[2].strip())

    writes_before = len(board.state()["evidence"])
    mutations_before = board.mutations()
    h = "d" * 64
    all_refusals = {
        "built, no hash": chief("loop", "set", "163", "--evidence", "built=release.tgz"),
        "built, no name": chief("loop", "set", "163", "--evidence", f"built={h}"),
        "built, two hashes": chief("loop", "set", "163", "--evidence", f"built=release.tgz {h} {'e' * 64}"),
        "installed, partial line": chief("loop", "set", "163", "--evidence", f"installed=app_id=com.shop.app host=mac artifact_sha256={h}"),
        "installed, free text": chief("loop", "set", "163", "--evidence", "installed=installed shop 2.4.1 on emulator-5554"),
        "installed, short hash": chief("loop", "set", "163", "--evidence", f"installed=app_id=a host=h artifact_sha256={h[:60]} readback_sha256={h}"),
        "installed, uppercase hash": chief("loop", "set", "163", "--evidence", f"installed=app_id=a host=h artifact_sha256={h} readback_sha256={h.upper()}"),
        "installed, one-line keys as JSON": chief("loop", "set", "163", "--evidence", 'installed={"text": "x", "device": "d", "head_sha": "abc"}'),
        "installed, JSON missing a field": chief("loop", "set", "163", "--evidence", 'installed=' + json.dumps({"app_id": "a", "host": "h", "artifact_sha256": h})),
    }
    err = {name: result[2] for name, result in all_refusals.items()}
    run.step("visibility case 3: built and installed evidence that cannot fill every contract field is refused before any Kanban write, naming the field or the exact form",
             all(r[0] == 1 for r in all_refusals.values()) and len(board.state()["evidence"]) == writes_before and board.mutations() == mutations_before
             and "no sha256" in err["built, no hash"] and "no name" in err["built, no name"] and "more than one sha256" in err["built, two hashes"]
             and "no readback_sha256" in err["installed, partial line"] and "no app_id, artifact_sha256, host, readback_sha256" in err["installed, free text"]
             and "artifact_sha256 must be 64 lowercase hex" in err["installed, short hash"] and "readback_sha256 must be 64 lowercase hex" in err["installed, uppercase hash"]
             and all("exactly these text fields" in err[n] for n in ("installed, one-line keys as JSON", "installed, JSON missing a field"))
             and all('--evidence \'installed={"app_id":"...","artifact_sha256":"<64hex>","host":"...","readback_sha256":"<64hex>"}\'' in err[n] for n in err if n.startswith("installed"))
             and all("never fills in readback_sha256" in err[n] for n in err if n.startswith("installed")), json.dumps(err)[:600])
    board.add_card("faker-kit:chief:164", "part 164", part_body(164, "in-progress", outcome="verified", prs="88"), assignee=CHIEF)
    verified = chief("loop", "set", "164", "--evidence", "verified=ran the suite on emulator-5554 v2.4.1")
    verified_row = [e for e in board.state()["evidence"] if e["task_id"] == card("faker-kit:chief:164")["id"] and e["kind"] == "other_result"]
    run.step("visibility case 3: verified one-line evidence still records name and receipt",
             verified[0] == 0 and [r["payload"]["name"] for r in verified_row] == ["faker-kit-verified"] and verified_row[0]["payload"]["receipt"]["device"] == "emulator-5554", verified[2].strip())

    Board(run)
    run.update_gh(lambda st: st.update(pulls=[]))
    quiet = project("loop", "check")
    quiet_chief = chief("loop", "check")
    run.step("visibility quiet: after the cutover a board where nothing is wrong prints no finding and both roles exit 0",
             quiet[0] == 0 and quiet_chief[0] == 0 and findings(quiet) == [] and findings(quiet_chief) == [], quiet[1].strip() + quiet_chief[1].strip())
