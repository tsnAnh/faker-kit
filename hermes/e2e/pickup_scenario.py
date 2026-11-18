from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, FOOTER, PROJECT, Board, head_of, json_of
from kanban_visibility_scenario import part_body

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

HERMES = Path(__file__).resolve().parent.parent
HOUR = 3600
SESSION_URL = "https://claude.ai/code/session_01ABCDEFGHJKMNPQRSTUVWXYZ"
STEER_SESSION = "session_01ABCDEFGHJKMNPQRSTUVWXYZ"
CRON_LIST = "  job-7 [active]\n    Name:      faker-kit-babysit\n    Schedule:  every 60m\n    Next run:  2026-11-17T10:00:00+00:00\n"


def stamp(moment: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))


def card_body(unit: str, goal: str | None, decisions: str | None, added: float, project: str = PROJECT, kind: str = "unit", fields: dict[str, str] | None = None,
              repeated: tuple[tuple[str, str], ...] = (), tail: tuple[str, ...] = ()) -> str:
    text = [goal if goal is not None else unit, f"Unit of {project}, run by {BOT}; serves no chief part.", "", f"Owner decisions: {decisions or 'none recorded'}",
            "Pull requests: none yet", "Sessions: none yet", "Status: handed-out; not waiting; updated unknown", *tail]
    head = {"Key": f"faker-kit:{project}:{unit}", "Kind": kind, "Project": project, "Bot": BOT, "Outcome": "merged-pr", "State": "handed-out",
            "Added": str(added), "Updated": str(added), **(fields or {})}
    rows = [f"{name}: {value}" for name, value in head.items()] + [f"{name}: {value}" for name, value in repeated]
    return "\n".join([*text, "", FOOTER, *rows])


def cron(_argv: list[str], _seconds: int) -> tuple[int, str]:
    return 0, CRON_LIST


def pickup_scenario(run: Run) -> None:
    guard = run.team / "guard.yaml"
    guard.write_text(guard.read_text().replace("max_in_flight_total: 3", "max_in_flight_total: 4"))
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    (run.team / "kanban-cutover.json").write_text(json.dumps({"at": run.clock, "board": "default", "parts": [], "units": {}}))

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF, tool=cron)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT, tool=cron)

    def owner(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=None, tool=cron)

    def key(unit: str, project_name: str = PROJECT) -> str:
        return f"faker-kit:{project_name}:{unit}"

    def add(unit: str, body: str, assignee: str = BOT, status: str = "ready", project_name: str = PROJECT) -> str:
        return board.add_card(key(unit, project_name), unit, body, assignee, status)

    def listed() -> list[str]:
        return [row["card"] for row in json_of(project("pickup", "status")).get("pickup", [])]

    def live_runs(unit: str) -> list[dict]:
        ident = board.by_key()[key(unit)]["id"]
        return [r for r in board.state()["runs"] if r["task_id"] == ident and r["ended_at"] is None]

    def status_of(unit: str, project_name: str = PROJECT) -> str:
        return str(board.by_key()[key(unit, project_name)]["status"])

    goal = "Add a keyboard shortcut for the search box and cover it in the shop tests"
    decisions = "shortcut is slash; no settings page"
    old, mid, new = (add(unit, card_body(unit, goal, decisions, added)) for unit, added in (("w-old", 5.0), ("w-mid", 10.0), ("w-new", 20.0)))
    part_record = board.add_card("faker-kit:chief:101", "part 101", part_body(101, "in-progress"), CHIEF)
    chief_old = add("chief-old", card_body("chief-old", goal, decisions, 1.0), CHIEF)
    chief_new = add("chief-new", card_body("chief-new", goal, decisions, 99999.0), CHIEF)
    chief_ids = {part_record, chief_old, chief_new}
    excluded = {
        "part card of the project": add("some-part", card_body("some-part", goal, decisions, 2.0, kind="part")),
        "card of another project": add("blog-card", card_body("blog-card", goal, decisions, 2.0, "blog"), project_name="blog"),
        "card with a pull request recorded": add("has-pr", card_body("has-pr", goal, decisions, 2.0, fields={"PRs": "7"})),
        "card with a Session line": add("has-session", card_body("has-session", goal, decisions, 2.0, repeated=(("Session", SESSION_URL),))),
        "card with a live run": add("has-run", card_body("has-run", goal, decisions, 2.0, fields={"Run": f"9 session {SESSION_URL}"})),
        "card with only a title": add("only-title", card_body("only-title", None, decisions, 2.0)),
        "card with no owner decisions": add("no-decisions", card_body("no-decisions", goal, None, 2.0)),
        "card already steered": add("was-steered", card_body("was-steered", goal, decisions, 2.0, tail=(
                         f"Session steer: asked session {STEER_SESSION} at {stamp(run.clock - 60)} for a closing summary.",))),
        "archived card": add("archived", card_body("archived", goal, decisions, 2.0), status="archived"),
    }

    naive = [t for t in board.state()["tasks"] if t["status"] == "ready"]
    naive_chief = {t["id"] for t in naive if t["assignee"] == CHIEF}
    run.step("pickup selection, failing run: a naive selector that takes every ready card on the fixture board "
             "picks all 3 chief-assigned cards (a part record, an old unit-like card, a new one) and the project's excluded cards",
             naive_chief == chief_ids and {excluded["part card of the project"], excluded["card with a pull request recorded"], excluded["card with a live run"]} <= {t["id"] for t in naive},
             f"naive selects {len(naive)} ready cards, {len(naive_chief)} of them the chief's")

    fresh = project("pickup", "status")
    state_file = run.team / "pickup.json"
    run.step("pickup stop switch: on a fresh kit home the default is off, nothing is written, and status "
             "prints enabled false, the babysit job id and its next run, and the candidate count per project",
             json_of(fresh).get("enabled") is False and not state_file.exists() and json_of(fresh).get("jobs") == {PROJECT: {"id": "job-7", "enabled": True, "next_run": "2026-11-17T10:00:00+00:00"}}
             and json_of(fresh).get("candidates") == {PROJECT: 3} and json_of(fresh).get("pickup") == [], fresh[1].strip())
    denied = project("pickup", "on")
    run.step("pickup stop switch: a project bot is refused pickup on and pickup off, and the switch stays off", denied[0] != 0 and "owner and the chief" in denied[2]
             and project("pickup", "off")[0] != 0 and not state_file.exists(), denied[2].strip())
    chief_view = chief("pickup", "status")
    run.step("pickup selection: the chief and the owner see a candidate count per project and the chief's own cards never count (3 workable project cards, 0 chief cards)",
             json_of(chief_view).get("candidates") == {PROJECT: 3} and json_of(owner("pickup", "status")).get("candidates") == {PROJECT: 3}, chief_view[1].strip())

    off_gate = listed()
    on = chief("pickup", "on")
    persisted = json.loads(state_file.read_text()) if state_file.exists() else {}
    run.step("pickup stop switch: the chief turns it on; one small file in the kit home records enabled, since and by", on[0] == 0 and json_of(on).get("enabled") is True
             and persisted.get("enabled") is True and persisted.get("by") == CHIEF and (state_file.stat().st_mode & 0o044) == 0o044, json.dumps(persisted))
    run.update_gh(lambda st: st["files"].__setitem__(".agents/dispatch.json", json.dumps({"max_in_flight": 5})))
    everything = listed()
    run.step("pickup selection, passing run: on the same fixture with room for all of them, the project bot is "
             "offered exactly its 3 workable unit cards, oldest first, and no chief card, part card, other project's card, "
             "card with a pull request, Session line, live run, title only, no decisions, steered or archived one",
             off_gate == [] and everything == [old, mid, new] and not set(everything) & chief_ids and not set(everything) & set(excluded.values()), json.dumps(everything))
    run.step("pickup selection: every excluded class has a case and none is selected", all(card not in everything for card in excluded.values()) and len(excluded) == 9, ", ".join(sorted(excluded)))
    run.update_gh(lambda st: st["files"].__setitem__(".agents/dispatch.json", json.dumps({"forbidden_paths": ["infra/"]})))

    fresh_process = subprocess.run([sys.executable, str(HERMES / "bin" / "faker_kit.py"), "pickup", "status"], capture_output=True, text=True, check=False,
                                   env={**run.env(), "HERMES_HOME": str(run.profile), "FAKER_KIT_HOME": str(run.team)})
    seen = json.loads(fresh_process.stdout) if fresh_process.returncode == 0 and fresh_process.stdout.strip() else {}
    run.step(
             "pickup stop switch: a fresh process (a restart) reads the switch as enabled and offers the same cards",
             seen.get("enabled") is True and [row["card"] for row in seen.get("pickup", [])] == [old, mid],
             json.dumps(seen)[:200])

    [add(f"busy-{i}", card_body(f"busy-{i}", goal, decisions, 3.0), status="running") for i in range(2)]
    owner("pickup", "off")
    launched = project("launch", "--brief-file", str(run.brief("w-new.md", "/faker-mode\nbuild it\n")))
    run.step(
             "pickup limits: with pickup off a launch is exactly what it was, even with 2 unit cards of the project running",
             launched[0] == 0 and json_of(launched).get("card", {}).get("claim") == "claimed"
             and status_of("w-new") == "running" and len(live_runs("w-new")) == 1, launched[2].strip() or launched[1][:160])
    run.step(
             "pickup stop switch: pickup off stopped the candidate lines at once and the state file says so",
             listed() == [] and json.loads(state_file.read_text()).get("enabled") is False, state_file.read_text())
    owner("pickup", "on")
    third = project("launch", "--brief-file", str(run.brief("w-old.md", "/faker-mode\nbuild the old one\n")))
    run.step("pickup limits: with 2 running unit cards of the project plus one launched, a further launch is "
             "refused, names the running cards and the free slots, claims nothing and starts nothing",
             third[0] != 0 and all(f"{ident} {unit}" in third[2] for ident, unit in ((board.by_key()[key("busy-0")]["id"], "busy-0"), (board.by_key()[key("busy-1")]["id"], "busy-1")))
             and "free slots: 0" in third[2] and status_of("w-old") == "ready" and live_runs("w-old") == [], third[2].strip())
    run.step("pickup limits: with no free slot the gate has no candidate to list", listed() == [], json.dumps(listed()))

    board.edit_card(key("busy-0"), status="done")
    board.edit_card(key("busy-1"), status="done")
    board.edit_card(key("w-new"), status="done")
    blog = [add(f"other-{i}", card_body(f"other-{i}", goal, decisions, 3.0, "blog"), status="running", project_name="blog") for i in range(4)]
    total = project("launch", "--brief-file", str(run.brief("w-old.md", "/faker-mode\nbuild the old one\n")))
    run.step("pickup limits: with 4 running unit cards in total (another project's) a launch is refused naming them, and the project's own count is 0 of 2",
             total[0] != 0 and "4 of 4 in total" in total[2] and "0 of 2 unit cards" in total[2] and all(ident in total[2] for ident in blog) and listed() == [], total[2].strip())
    board.edit_card(key("other-0", "blog"), status="done")
    run.step("pickup limits: one free slot caps the candidate lines at one, the oldest", listed() == [old], json.dumps(listed()))
    board.edit_card(key("other-1", "blog"), status="done")
    run.step("pickup limits: two free slots list two candidates, oldest first", listed() == [old, mid], json.dumps(listed()))
    run.update_gh(lambda st: st["files"].__setitem__(".agents/dispatch.json", json.dumps({"max_in_flight": 1})))
    board.edit_card(key("busy-0"), status="running")
    run.step("pickup limits: max_in_flight from the repository's dispatch.json replaces the default of 2 (1 running of 1 leaves no slot)", listed() == [], json.dumps(listed()))
    board.edit_card(key("busy-0"), status="done")
    run.update_gh(lambda st: st["files"].__setitem__(".agents/dispatch.json", json.dumps({"forbidden_paths": ["infra/"]})))

    stuck_unit, pr_unit = "stuck-unit", "pr-unit"
    long_ago = stamp(run.clock - 2 * HOUR)
    recent = stamp(run.clock - 10 * 60)

    def running_steered(unit: str, at: str) -> str:
        ident = add(unit, card_body(unit, goal, decisions, 4.0, fields={"Run": f"4 session {SESSION_URL}"}, repeated=(("Session", SESSION_URL),),
                                    tail=(f"Session steer: asked session {STEER_SESSION} at {at} for a closing summary.",)), status="ready")
        board.leave_claim(key(unit), url="https://github.com/acme/shop/pull/1", head=head_of(1), commit=head_of(2))
        return ident

    stuck = running_steered(stuck_unit, long_ago)
    running_steered("fresh-steer", recent)
    running_steered(pr_unit, long_ago)
    run.update_gh(lambda st: st["pulls"].append({**run.pull(31, head_of(31)), "body": f"Unit: {pr_unit}\n\nthe fix"}))
    before = board.verbs()
    owner("pickup", "off")
    quiet_reconcile = project("loop", "reconcile")
    run.step(
             "pickup failure rule: with pickup off a steered unit with no pull request is left as it is (nothing is blocked)",
             quiet_reconcile[0] == 0 and status_of(stuck_unit) == "running" and "block" not in [v[0] for v in board.verbs()[len(before):]],
             quiet_reconcile[2].strip()[:160])
    owner("pickup", "on")
    first_pass = project("loop", "reconcile")
    out = json_of(first_pass)
    calls = [v[0] for v in board.verbs()[len(before):]]
    events = [e for e in board.state()["events"] if e["task_id"] == stuck and e["kind"] == "blocked"]
    run.step("pickup failure rule: a unit steered once an hour ago with still no pull request is blocked through "
             "the supported path: its claim is released first (reclaim), then block, with a reason naming the unit; never forced, completed or unblocked",
             first_pass[0] == 0 and status_of(stuck_unit) == "blocked" and live_runs(stuck_unit) == [] and calls.index("reclaim") < calls.index("block")
             and not {"complete", "unblock"} & set(calls) and out.get("blocked", [{}])[0].get("unit") == stuck_unit
             and len(events) == 1 and stuck_unit in events[0]["payload"]["reason"] and events[0]["payload"]["kind"] == "needs_input", json.dumps(out.get("blocked"))[:240])
    run.step("pickup failure rule: a unit steered ten minutes ago is given the grace period, and a unit that opened a pull request is never blocked",
             status_of("fresh-steer") == "running" and status_of(pr_unit) == "running" and [b["unit"] for b in out.get("blocked", [])] == [stuck_unit], json.dumps(out.get("blocked")))
    again = json_of(project("loop", "reconcile"))
    run.step("pickup failure rule: the next pass blocks nothing again, and the blocked card is not a candidate and a launch of it is refused, so it never gets a second session",
             again.get("blocked") == [] and stuck not in listed()
             and project("launch", "--brief-file", str(run.brief(f"{stuck_unit}.md", "/faker-mode\nretry\n")))[2].count("is blocked, not ready") == 1
             and len(board.state()["runs"]) == len({r["id"] for r in board.state()["runs"]}) and status_of(stuck_unit) == "blocked", json.dumps(again.get("blocked")))

    check = project("loop", "check")
    found = json.loads(check[1])["findings"] if check[1].strip() else []
    flagged = {f["card"]: f for f in found if f["class"] == "needs_brief"}
    wanted = {excluded["card with only a title"], excluded["card with no owner decisions"]}
    run.step("pickup loop check: a ready unit card that is only a title, or has no decisions, is listed with the "
             "fix ask the chief for a brief or owner decisions, and does not need action; part cards, chief cards, workable cards and other projects' cards produce no finding",
             set(flagged) == wanted and all(f["fix"] == "ask the chief for a brief or owner decisions" and f["needs_action"] is False for f in flagged.values())
             and not ({part_record, chief_old, chief_new, excluded["part card of the project"], excluded["card of another project"], old, mid, new} & set(flagged)),
             json.dumps(sorted(flagged)))
    chief_check = chief("loop", "check")
    run.step(
             "pickup loop check: the chief sees no needs_brief finding for the project's cards",
             all(f["class"] != "needs_brief" for f in (json.loads(chief_check[1])["findings"] if chief_check[1].strip() else [])), chief_check[1].strip()[:120])
    run.step("pickup never touches a chief card: no claim, block, edit or archive ever named a chief-assigned card",
             all(t["status"] == "ready" and t["body"] == body for t, body in (
                 (next(x for x in board.state()["tasks"] if x["id"] == i), next(x for x in naive if x["id"] == i)["body"]) for i in chief_ids)),
             ", ".join(sorted(chief_ids)))
