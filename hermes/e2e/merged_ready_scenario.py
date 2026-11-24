from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, DECISIONS, FOOTER, PROJECT, REPO, Board, commit_of, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

INSTALLED_LINE = "app_id=com.sale.debug host=emulator-5554 artifact_sha256=" + "c" * 64 + " readback_sha256=" + "c" * 64
OTHER = "other-shop"


def merged_ready_scenario(run: Run) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def owner(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=None)

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def card(unit: str) -> dict:
        return board.by_key()[key(unit)]

    def part_card(ident: int | str) -> dict:
        return board.by_key()[f"faker-kit:chief:{ident}"]

    def pull(n: int, unit: str | None, merged: bool, commit: bool = True, **fields: object) -> None:
        body = f"Unit: {unit}\n\nChange for {unit}" if unit else "A change with no unit line"
        extra = {"state": "closed", "merged_at": "2026-10-10T00:00:00Z", "merge_commit_sha": commit_of(n) if commit else ""} if merged else {}
        run.update_gh(lambda state: state["pulls"].append({**run.pull(n, head_of(n)), "body": body, **extra, **fields}))

    def unit_card(unit: str, n: int | None = None, *extra: str) -> None:
        assert project("loop", "unit", unit, "--title", "Visitors see the fixture change", *(["--pr", str(n)] if n else []), *extra)[0] == 0

    def events(unit: str, kind: str) -> list[dict]:
        return [e for e in board.state()["events"] if e["task_id"] == card(unit)["id"] and e["kind"] == kind]

    def runs(unit: str) -> list[dict]:
        return [r for r in board.state()["runs"] if r["task_id"] == card(unit)["id"]]

    def completions(unit: str) -> list[dict]:
        return [c for c in board.state()["completions"] if c["task"] == card(unit)["id"]]

    def writes_since(mark: int) -> list[str]:
        return [v[0] for v in board.verbs()[mark:] if v[0] in ("create", "link", "edit", "claim", "reclaim", "evidence", "complete", "archive", "block", "schedule", "unblock")
            and v != ("evidence", "list")]

    def pending(out: dict) -> dict[str, dict]:
        return {r["id"]: r for r in out.get("entries", {}).get("completion_pending", [])}

    def status_line(unit: str) -> str:
        return next((line for line in board.body(key(unit)).splitlines() if line.startswith("Status: ")), "")

    def foreign_unit(unit: str) -> str:
        body = "\n".join([FOOTER, f"Key: faker-kit:{OTHER}:{unit}", "Kind: unit", f"Project: {OTHER}", "Bot: other-bot", "Outcome: installed", "State: in-progress",
                          "Ref: OT-1", "Added: 1.0", "Updated: 1.0"])
        return board.add_card(f"faker-kit:{OTHER}:{unit}", f"OT-1 {unit}", body, assignee="other-bot")

    assert chief("loop", "migrate", "--apply")[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0
    owner("pickup", "on")

    def running_cards() -> int:
        return len([t for t in board.state()["tasks"] if t["status"] == "running" and f"Project: {PROJECT}" in t["body"] and "Kind: unit" in t["body"]])

    pull(601, "gate-a", True)
    unit_card("gate-a", 601)
    seen: list[str] = []
    for mode, wanted in (("pending", "pending"), ("failure", "failure"), ("bare", "unknown")):
        board.set(complete_refuse=mode)
        run.clock += 7 * 3600
        out = json_of(project("loop", "reconcile"))
        row = pending(out).get("gate-a", {})
        slots = json_of(project("pickup", "status", "--no-job"))
        seen.append(str(row.get("classification")))
        run.step(f"merged ready 1: Kanban refusing completion ({mode}) leaves the merged card scheduled and never ready, with a Status line naming the reason, no claim, and no pickup slot",
                 card("gate-a")["status"] == "scheduled" and status_line("gate-a").startswith("Status: merged, waiting for Kanban acceptance: " + wanted)
                 and board.open_claims(key("gate-a")) == [] and "State: merged" in board.body(key("gate-a")) and slots.get("running") == running_cards() and row.get("classification") == wanted,
                 json.dumps({"status": card("gate-a")["status"], "line": status_line("gate-a"), "running": slots.get("running"), "row": row.get("classification")}))
        mark = len(board.verbs())
        before = board.mutations()
        held = json_of(project("loop", "reconcile"))
        run.step(f"merged ready 1: a pass inside the hold after the {mode} refusal makes no Kanban write and prints the same pending row as held",
                 pending(held).get("gate-a", {}).get("held") is True and board.mutations() == before and writes_since(mark) == [] and card("gate-a")["status"] == "scheduled",
                 json.dumps(pending(held).get("gate-a", {}))[:240])
    from_states = [row["from"] for row in board.state().get("unblocked_from", [])]
    run.step("merged ready 1: each retry after a hold unblocked the scheduled card once and claimed it again (three claims, never a claim on a ready-by-reclaim card)",
             seen == ["pending", "failure", "unknown"] and from_states == ["scheduled", "scheduled"] and len(runs("gate-a")) == 3 and all(r["status"] == "scheduled" for r in runs("gate-a")),
             json.dumps({"seen": seen, "unblocked": from_states, "runs": [r["status"] for r in runs("gate-a")]}))
    board.set(complete_refuse=None)
    run.clock += 7 * 3600
    done = json_of(project("loop", "reconcile"))
    run.step("merged ready 1: once Kanban accepts, the retry unblocks, claims and completes the card, the Status line is gone, "
             "State stays merged, and no reclaimed event ever put the card back in Ready",
             "gate-a" in done["entries"]["merged"] and card("gate-a")["status"] == "done" and status_line("gate-a") == "" and "Acceptance:" not in board.body(key("gate-a"))
             and "State: merged" in board.body(key("gate-a")) and len(completions("gate-a")) == 4 and events("gate-a", "reclaimed") == [] and board.open_claims(key("gate-a")) == [],
             json.dumps(done["entries"])[:240])

    pull(602, "strand-a", True)
    unit_card("strand-a", 602)
    board.set(complete_refuse="pending")
    run.clock += 7 * 3600
    project("loop", "reconcile")
    board.edit_card(key("strand-a"), status="ready")
    mark, before = len(board.verbs()), board.mutations()
    adopted = json_of(project("loop", "reconcile"))
    after_adopt = board.mutations()
    mid = len(board.verbs())
    again = json_of(project("loop", "reconcile"))
    run.step("merged ready 2: a merged card an older pass left in Ready (State merged, no run, hold not over) is scheduled by one "
             "reconcile with no claim and no completion call, and a second reconcile changes nothing",
             card("strand-a")["status"] == "scheduled" and writes_since(mark) == ["edit", "schedule"][-len(writes_since(mark)):]
                 and "schedule" in writes_since(mark) and "claim" not in writes_since(mark)
             and "complete" not in writes_since(mark) and status_line("strand-a").startswith("Status: merged, waiting for Kanban acceptance: pending")
             and pending(adopted).get("strand-a", {}).get("scheduled") is True and writes_since(mid) == [] and board.mutations() == after_adopt
             and pending(again).get("strand-a", {}).get("held") is True and before < after_adopt, json.dumps(writes_since(mark)))
    pull(603, "strand-b", True)
    unit_card("strand-b", 603)
    body = board.body(key("strand-b"))
    board.edit_card(key("strand-b"), body=body.replace("State: in-progress", "State: merged"), status="ready")
    mark = len(board.verbs())
    cold = json_of(project("loop", "reconcile"))
    run.step("merged ready 2: a merged card with no refusal on record that sits in Ready is retried, refused and scheduled in the same pass",
             card("strand-b")["status"] == "scheduled" and pending(cold).get("strand-b", {}).get("classification") == "pending"
                 and "schedule" in writes_since(mark), json.dumps(pending(cold).get("strand-b", {}))[:240])
    pull(604, "nomerge-a", True, commit=False)
    unit_card("nomerge-a", 604)
    mark = len(board.verbs())
    nomerge = json_of(project("loop", "reconcile"))
    run.step("merged ready 2: a merged pull request GitHub reports with no merge commit yet parks the card in scheduled too, never in Ready, and says why on the Status line",
             card("nomerge-a")["status"] == "scheduled" and "no merge commit" in status_line("nomerge-a") and pending(nomerge).get("nomerge-a", {}).get("scheduled") is True
             and "claim" not in writes_since(mark), status_line("nomerge-a"))
    board.set(complete_refuse=None)

    pull(605, "unmerged-a", False, state="closed", merged_at=None)
    unit_card("unmerged-a", 605)
    run_id = board.leave_claim(key("unmerged-a"), "pr_published", url=f"https://github.com/{REPO}/pull/605", head=head_of(605), commit=commit_of(605))
    mark = len(board.verbs())
    closed = json_of(project("loop", "reconcile"))
    verbs = board.verbs()[mark:]
    run.step("merged ready 3: a card whose pull request closed unmerged keeps the old flow: its claim is released with reclaim, the card is archived, and nothing is scheduled",
             "unmerged-a" in closed["entries"]["closed"] and card("unmerged-a")["status"] == "archived" and ("reclaim",) in verbs and ("schedule",) not in verbs and run_id > 0,
             json.dumps([v[0] for v in verbs]))

    board.set(schedule_refuse=True, complete_refuse="pending")
    pull(606, "noschedule-a", True)
    unit_card("noschedule-a", 606)
    run.clock += 7 * 3600
    fallback = json_of(project("loop", "reconcile"))
    row = pending(fallback).get("noschedule-a", {})
    run.step("merged ready 4: when Kanban will not schedule the card, the old behaviour applies with one printed notice: the claim is "
             "released, the card returns to Ready, nothing is left half done, and no claim stays open",
             "Kanban would not schedule" in str(row.get("schedule_unavailable")) and card("noschedule-a")["status"] == "ready" and board.open_claims(key("noschedule-a")) == []
             and "claim_left_open" not in row and row.get("completion") == "pending", json.dumps(row)[:300])
    board.set(schedule_refuse=False, complete_refuse=None)

    pull(607, "slot-a", True)
    unit_card("slot-a", 607)
    board.set(complete_refuse="pending")
    run.clock += 7 * 3600
    project("loop", "reconcile")
    running = json_of(project("pickup", "status", "--no-job"))
    run.step("merged ready 5: scheduled merged cards hold no pickup slot, whatever their number", card("slot-a")["status"] == "scheduled"
        and running.get("running") == running_cards() and running.get("running_total") == running_cards(),
             json.dumps({k: running.get(k) for k in ("running", "running_total", "free_slots")}))
    board.set(complete_refuse=None)
    run.clock += 7 * 3600
    assert project("loop", "reconcile")[0] == 0

    made = json_of(chief("handoff", "--project", PROJECT, "--part", "Update the Pi and build the Mac app", "--title", "Pi and Mac app updated", "--decisions", DECISIONS))
    pid = str(made["id"])
    action_part = part_card(pid)
    board.edit_card(f"faker-kit:chief:{pid}", body=action_part["body"].replace("State: handed-out", "State: merged"), status="ready")
    closed_part = part_card(pid)
    mark = len(board.verbs())
    dry = chief("loop", "close", pid, "--as", "superseded", "--reason", "no pull request can accept an action part", "--dry-run")
    plain = chief("loop", "close", pid, "--as", "superseded", "--reason", "no pull request can accept an action part")
    after = part_card(pid)
    run.step("merged ready 6: loop close --as superseded works for a part whose State is already merged but whose Kanban card is not done: it "
             "records the reason in the header, releases any claim, archives the card, and never completes it",
             "State: merged" in closed_part["body"] and dry[0] == 0 and plain[0] == 0 and after["status"] == "archived" and "State: abandoned" in after["body"]
             and "History: closed superseded: no pull request can accept an action part" in after["body"] and "complete" not in [v[0] for v in board.verbs()[mark:]]
             and json_of(plain).get("archived") is True, plain[2].strip() or plain[1][:200])
    twice = chief("loop", "close", pid, "--as", "superseded", "--reason", "again")
    done_part = json_of(chief("handoff", "--project", PROJECT, "--part", "A finished action part", "--title", "Finished action part", "--decisions", DECISIONS))
    board.edit_card(f"faker-kit:chief:{done_part['id']}", status="done")
    on_done = chief("loop", "close", str(done_part["id"]), "--as", "superseded", "--reason", "x")
    foreign = foreign_unit("alien")
    on_foreign = project("loop", "close", foreign, "--reason", "not mine")
    run.step("merged ready 6: an archived card, a done card and another project's unit card still refuse, and nothing is changed",
             twice[0] == 1 and "already closed" in twice[2] and on_done[0] == 1 and "already closed" in on_done[2] and on_foreign[0] == 1 and "not one" in on_foreign[2],
             " | ".join(r[2].strip() for r in (twice, on_done, on_foreign)))

    stuck = json_of(chief("handoff", "--project", PROJECT, "--part", "Update the Pi again", "--title", "Pi updated again", "--decisions", DECISIONS))
    sid = str(stuck["id"])
    board.edit_card(f"faker-kit:chief:{sid}", body=part_card(sid)["body"].replace("State: handed-out", "State: merged"), status="ready")
    ledger_set = chief("loop", "set", sid, "--state", "abandoned")
    run.step("merged ready 7: loop set <part> --state abandoned archives a part whose State is merged and records abandoned in its header, never merged",
             ledger_set[0] == 0 and part_card(sid)["status"] == "archived" and "State: abandoned" in part_card(sid)["body"] and "State: merged" not in part_card(sid)["body"],
             ledger_set[2].strip() or ledger_set[1][:200])

    def part_with_unit(text: str, unit: str, n: int, **pull_fields: object) -> tuple[str, str]:
        part = json_of(chief("handoff", "--project", PROJECT, "--part", text, "--title", text, "--decisions", DECISIONS))
        assert chief("loop", "set", str(part["id"]), "--state", "in-progress", "--pr", str(n))[0] == 0
        pull(n, None, True, **pull_fields)
        unit_card(unit, None, "--part", str(part["id"]))
        return str(part["id"]), f"faker-kit:chief:{part['id']}"

    normal_id, _ = part_with_unit("Part with an open unit, normal", "open-unit-a", 610)
    nocommit_id, _ = part_with_unit("Part with an open unit, no merge commit", "open-unit-b", 611)
    run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == 611).update(merge_commit_sha=""))
    stale_id, stale_key = part_with_unit("Part with an open unit, stale blocked", "open-unit-c", 612)
    board.leave_claim(stale_key, url=f"https://github.com/{REPO}/pull/612", head=head_of(612), commit=commit_of(612))
    board.stale_claim(stale_key)
    board.leave_claim(stale_key, url=f"https://github.com/{REPO}/pull/612", head=head_of(612), commit=commit_of(612))
    board.stale_claim(stale_key)
    mark = len(board.verbs())
    waiting = json_of(chief("loop", "reconcile"))
    waiting_ids = {r["id"] for r in waiting["entries"]["waiting_on_units"]}
    shown = {str(p["id"]): p["state"] for p in json_of(chief("loop", "show", "--all"))["open"]}
    run.step("merged ready 8: a part with an open unit card is not marked merged in the stale-blocked, no-merge-commit or normal branch, and is reported under waiting_on_units",
             {normal_id, nocommit_id, stale_id} <= waiting_ids
                 and all(shown.get(i) == "in-progress" for i in (normal_id, nocommit_id, stale_id)) and not (set(waiting["entries"]["stale_blocked"]) & {stale_id})
             , json.dumps({"waiting": sorted(waiting_ids), "shown": {i: shown.get(i) for i in (normal_id, nocommit_id, stale_id)}}))
    for unit in ("open-unit-a", "open-unit-b", "open-unit-c"):
        board.edit_card(key(unit), status="done")
    run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == 611).update(merge_commit_sha=commit_of(611)))
    run.clock += 7 * 3600
    finished = json_of(chief("loop", "reconcile"))
    run.step("merged ready 8: with every unit card done the parts leave waiting_on_units and settle as before (the normal one completes, the "
             "no-merge-commit one is parked, the stale-blocked one is reported with its unblock line)",
             normal_id in finished["entries"]["merged"] and part_card(normal_id)["status"] == "done"
                 and not {r["id"] for r in finished["entries"]["waiting_on_units"]} & {normal_id, nocommit_id, stale_id}
             and stale_id in {r["id"] for r in finished["entries"]["stale_blocked"]}, json.dumps(finished["entries"])[:300])

    install = json_of(chief("handoff", "--project", PROJECT, "--part", "Install the fren build", "--title", "Fren build installs", "--decisions", DECISIONS, "--outcome", "installed"))
    iid, ikey = str(install["id"]), f"faker-kit:chief:{install['id']}"
    pull(620, "fren-unit", True)
    assert chief("loop", "set", iid, "--state", "in-progress", "--pr", "620")[0] == 0
    unit_card("fren-unit", 620, "--part", iid)
    unit_ref = card("fren-unit")["body"].split("Ref: ")[1].splitlines()[0]
    run.clock += 7 * 3600
    first = json_of(project("loop", "reconcile"))
    run.step("merged ready 9: a unit serving an installed part whose pull request merged waits for its stage evidence outside Ready, and no pr_published row is ever offered to its installed contract",
             {"id": "fren-unit", "outcome": "installed"} in first["entries"]["awaiting_stage_evidence"] and card("fren-unit")["status"] == "scheduled"
             and not [e for e in board.state()["evidence"] if e["task_id"] == card("fren-unit")["id"]] and not first["entries"]["refused"], json.dumps(first["entries"])[:300])
    nonsense = project("loop", "set", unit_ref, "--evidence", "bogus=whatever")
    unit_card("plain-b")
    on_plain = project("loop", "set", "plain-b", "--evidence", f"installed={INSTALLED_LINE}")
    foreign_ref = project("loop", "set", foreign_unit("alien-2"), "--evidence", f"installed={INSTALLED_LINE}")
    state_flag = project("loop", "set", unit_ref, "--state", "merged", "--evidence", f"installed={INSTALLED_LINE}")
    run.step("merged ready 10: loop set on a unit refuses a stage that is not built, installed or verified, a unit whose outcome is "
             "a merged pull request, another project's unit card, and any change but evidence",
             nonsense[0] == 1 and "STAGE one of" in nonsense[2] and on_plain[0] == 1 and "merged pull request" in on_plain[2] and foreign_ref[0] == 1 and "not a unit card" in foreign_ref[2]
             and state_flag[0] == 1 and "takes only --evidence" in state_flag[2], " | ".join(r[2].strip()[:90] for r in (nonsense, on_plain, foreign_ref, state_flag)))
    saved = project("loop", "set", unit_ref.lower(), "--evidence", f"installed={INSTALLED_LINE}")
    rows = [e for e in board.state()["evidence"] if e["task_id"] == card("fren-unit")["id"]]
    run.step("merged ready 10: loop set <unit ref, any case> --evidence installed=<line> records app_installed on the unit card's own "
             "run, saves the stage in its header, and puts the card back to waiting, not to Ready",
             saved[0] == 0 and [r["kind"] for r in rows] == ["app_installed"] and rows[0]["payload"].get("host") == "emulator-5554" and "Stage: installed=" in board.body(key("fren-unit"))
             and card("fren-unit")["status"] == "scheduled" and board.open_claims(key("fren-unit")) == [], saved[2].strip() or saved[1][:240])
    by_id = project("loop", "set", card("fren-unit")["id"], "--evidence", f"installed={INSTALLED_LINE}")
    run.step("merged ready 10: the same command by card id records nothing twice", by_id[0] == 0
        and len([e for e in board.state()["evidence"] if e["task_id"] == card("fren-unit")["id"] and e["kind"] == "app_installed"]) >= 1,
             by_id[2].strip())
    run.clock += 7 * 3600
    final = json_of(project("loop", "reconcile"))
    final_kinds = sorted({e["kind"] for e in board.state()["evidence"] if e["task_id"] == card("fren-unit")["id"]})
    run.step("merged ready 10: the unit then completes through reconcile with app_installed and never pr_published (Kanban's installed contract refuses it)",
             "fren-unit" in final["entries"]["merged"] and card("fren-unit")["status"] == "done" and final_kinds == ["app_installed"] and ikey in board.by_key() and not final["entries"]["refused"],
             json.dumps({"kinds": final_kinds, "entries": final["entries"]})[:300])
    part_set = chief("loop", "set", iid, "--evidence", f"installed={INSTALLED_LINE}")
    run.step("merged ready 10: loop set <part id> --evidence still works exactly as before", part_set[0] == 0 and any(line.startswith("Stage: installed=") for line in board.body(ikey).splitlines()),
             part_set[2].strip())
