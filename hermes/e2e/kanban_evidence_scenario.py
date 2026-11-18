from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, DECISIONS, PROJECT, REPO, Board, commit_of, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

BUILT = json.dumps({"name": "release.tgz", "sha256": "a" * 64})
WRITES = ("claim", "reclaim", "complete", "edit", "create", "archive", "block", "link", "heartbeat")


def evidence_scenario(run: Run) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def url(n: int) -> str:
        return f"https://github.com/{REPO}/pull/{n}"

    def add_pr(n: int, unit: str, **fields: object) -> None:
        run.update_gh(lambda state: state["pulls"].append({**run.pull(n, head_of(n)), "body": f"Unit: {unit}\n\nChange for {unit}", **fields}))

    def merged_pr(n: int, unit: str) -> None:
        add_pr(n, unit, state="closed", merged_at="2026-10-10T00:00:00Z", merge_commit_sha=commit_of(n))

    def new_card(unit: str, n: int) -> str:
        assert project("loop", "unit", unit, "--pr", str(n))[0] == 0
        return board.by_key()[key(unit)]["id"]

    def rows(card_key: str, run_id: int | None = None) -> list[dict]:
        ident = board.by_key()[card_key]["id"]
        return [e for e in board.state()["evidence"] if e["task_id"] == ident and (run_id is None or e["run_id"] == run_id)]

    def runs(card_key: str) -> list[dict]:
        ident = board.by_key()[card_key]["id"]
        return [r for r in board.state()["runs"] if r["task_id"] == ident]

    def kinds(found: list[dict]) -> dict[str, int]:
        return {kind: len([e for e in found if e["kind"] == kind]) for kind in sorted({e["kind"] for e in found})}

    def writes_since(mark: int) -> list[tuple[str, ...]]:
        return [v for v in board.verbs()[mark:] if v[0] in WRITES or v == ("evidence", "add")]

    def refused_row(out: dict, card_id: str) -> dict:
        return next((r for r in out["entries"]["refused"] if r["card"] == card_id), {})

    assert chief("loop", "migrate", "--apply")[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0
    refusals_file = run.team / "refusals" / f"{BOT}.json"

    merged_pr(201, "dup-a")
    card_a = new_card("dup-a", 201)
    board.set(complete_refuse="pending")
    first = json_of(project("loop", "reconcile"))
    after_first, mark = board.mutations(), len(board.verbs())
    ticks = [json_of(project("loop", "reconcile")) for _ in range(9)]
    first_row, last_row = refused_row(first, card_a), refused_row(ticks[-1], card_a)
    run.step("evidence dedupe: a merged pull request whose completion Kanban always refuses, reconciled 10 times, ends with one run and one pr_published and one pr_merged row",
             len(runs(key("dup-a"))) == 1 and kinds(rows(key("dup-a"))) == {"pr_merged": 1, "pr_published": 1} and board.by_key()[key("dup-a")]["status"] == "ready"
             and board.open_claims(key("dup-a")) == [], json.dumps(kinds(rows(key("dup-a")))))
    run.step("evidence dedupe: ticks 2 to 10 make no Kanban write at all (no claim, evidence, complete, or reclaim), and the refusal stays visible with its classification and head",
             board.mutations() == after_first and writes_since(mark) == [] and first_row.get("classification") == "pending" and first_row.get("held") is None
             and all(refused_row(t, card_a).get("held") is True and refused_row(t, card_a).get("classification") == "pending" for t in ticks)
             and last_row.get("head") == head_of(201) and last_row.get("retry_after", 0) > run.clock, json.dumps(last_row))

    moved = f"{4201:040x}"
    run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == 201).update(head=moved))
    pushed = json_of(project("loop", "reconcile"))
    again = board.mutations()
    json_of(project("loop", "reconcile"))
    new_row = refused_row(pushed, card_a)
    run.step("evidence dedupe: a new head tries again exactly once, with its own run and its own rows, and a repeat after it writes nothing",
             len(runs(key("dup-a"))) == 2 and kinds(rows(key("dup-a"), runs(key("dup-a"))[-1]["id"])) == {"pr_merged": 1, "pr_published": 1}
             and new_row.get("held") is None and new_row.get("head") == moved
             and board.mutations() == again and len(rows(key("dup-a"))) == 4, json.dumps(new_row))

    board.set(complete_refuse=None)
    still = json_of(project("loop", "reconcile"))
    run.step("evidence dedupe: the cause clearing on Kanban's side is not visible to the kit, so the card stays held and nothing is written until the hold passes",
             board.mutations() == again and refused_row(still, card_a).get("held") is True and board.by_key()[key("dup-a")]["status"] == "ready", json.dumps(refused_row(still, card_a)))
    run.clock = float(refused_row(still, card_a)["retry_after"]) + 1
    done = json_of(project("loop", "reconcile"))
    final = rows(key("dup-a"), runs(key("dup-a"))[-1]["id"])
    run.step("evidence dedupe: once the hold has passed and Kanban accepts, the next tick completes the card on its own run with pr_published and pr_merged once each",
             "dup-a" in done["entries"]["merged"] and board.by_key()[key("dup-a")]["status"] == "done" and len(runs(key("dup-a"))) == 3 and kinds(final) == {"pr_merged": 1, "pr_published": 1}
             and final[1]["payload"] == {"pr_url": url(201), "head_sha": moved, "merge_commit": commit_of(201)} and "State: merged" in board.body(key("dup-a"))
             and card_a not in json.loads(refusals_file.read_text()), json.dumps(done["entries"]["merged"]))

    add_pr(202, "event-b")
    new_card("event-b", 202)
    for _ in range(5):
        assert project("loop", "reconcile")[0] == 0
    run.step("evidence dedupe: the same open ready pull request at the same head delivered 5 times in a row adds one pr_published row",
             kinds(rows(key("event-b"))) == {"pr_published": 1} and len(runs(key("event-b"))) == 1 and board.by_key()[key("event-b")]["status"] == "running",
             json.dumps(kinds(rows(key("event-b")))))

    merged_pr(203, "ok-c")
    card_c = new_card("ok-c", 203)
    shipped = json_of(project("loop", "reconcile"))
    run.step("evidence dedupe: a card that completes on its first attempt gets one run, pr_published then pr_merged, state done, and no refusal memory",
             "ok-c" in shipped["entries"]["merged"] and board.by_key()[key("ok-c")]["status"] == "done" and len(runs(key("ok-c"))) == 1
             and [e["kind"] for e in rows(key("ok-c"))] == ["pr_published", "pr_merged"] and (not refusals_file.exists() or card_c not in json.loads(refusals_file.read_text())),
             json.dumps([e["kind"] for e in rows(key("ok-c"))]))

    add_pr(204, "pile-d", state="closed", merged_at=None)
    card_d = new_card("pile-d", 204)

    def pile(card_id: str) -> None:
        data = board.state()
        for run_id in range(101, 107):
            data["runs"].append({"id": run_id, "task_id": card_id, "profile": BOT, "started_at": 1, "ended_at": 2, "status": "reclaimed"})
            for kind, payload in (("pr_published", {"pr_url": url(204), "head_sha": head_of(204)}), ("pr_merged", {"pr_url": url(204), "head_sha": head_of(204), "merge_commit": commit_of(204)})):
                data["evidence"].append({"id": len(data["evidence"]) + 1, "task_id": card_id, "run_id": run_id, "kind": kind, "payload": payload})
        board.path.write_text(json.dumps(data))

    pile(card_d)
    board.edit_card(key("pile-d"), status="done")
    before_state, mark = board.state(), len(board.verbs())
    plain = project("loop", "check")
    listed = project("loop", "check", "--all")
    found = [f for f in json.loads(listed[1])["findings"] if f["class"] == "duplicate_evidence" and f["card"] == card_d]
    groups = {g["kind"]: g for g in found[0]["duplicates"]} if found else {}
    after_state = board.state()
    run.step("evidence dedupe: loop check --all lists the card's duplicate evidence rows with kind, count and runs, exits 0, and changes nothing; plain loop check does not read them",
             plain[0] == 0 and "duplicate_evidence" not in plain[1] and listed[0] == 0 and len(found) == 1 and found[0]["card"] == card_d and found[0]["needs_action"] is False
             and {k: (g["count"], g["runs"]) for k, g in groups.items()} == {"pr_published": (6, list(range(101, 107))), "pr_merged": (6, list(range(101, 107)))}
             and "pr_published x6" in found[0]["line"] and "pr_merged x6" in found[0]["line"] and "add and list only" in found[0]["fix"]
             and after_state["evidence"] == before_state["evidence"] and after_state["runs"] == before_state["runs"] and board.mutations() == before_state["mutations"]
             and writes_since(mark) == [], json.dumps(found[0]["line"]))
    removals = {v for v in board.verbs() if v[0] == "evidence" and v != ("evidence", "list") and v != ("evidence", "add")}
    run.step("evidence dedupe: Kanban offers the kit evidence add and list only, so no pass removes or supersedes a row", removals == set(), json.dumps(sorted(removals)))

    made = json_of(chief("handoff", "--project", PROJECT, "--part", "Part with stage evidence", "--decisions", DECISIONS, "--outcome", "built"))
    part_key = f"faker-kit:chief:{made['id']}"
    assert chief("loop", "set", str(made["id"]), "--state", "in-progress")[0] == 0
    mutations, mark = board.mutations(), len(board.verbs())
    recorded = chief("loop", "set", str(made["id"]), "--evidence", f"built={BUILT}")
    after_one, runs_one = board.mutations(), len(runs(part_key))
    repeated = chief("loop", "set", str(made["id"]), "--evidence", f"built={BUILT}")
    run.step("evidence dedupe: recording the same stage evidence twice adds one artifact_built row, and the repeat claims nothing",
             recorded[0] == 0 and repeated[0] == 0 and kinds(rows(part_key)) == {"artifact_built": 1} and len(runs(part_key)) == runs_one == 1 and after_one > mutations
             and not any(v[0] in ("claim", "reclaim") for v in board.verbs()[mark:][-6:]) and board.open_claims(part_key) == [], recorded[2].strip() + repeated[2].strip())

    live = board.leave_claim(part_key, url=url(205), head=head_of(205), commit=commit_of(205))
    assert chief("loop", "set", str(made["id"]), "--evidence", f"built={json.dumps({'name': 'second.tgz', 'sha256': 'b' * 64})}")[0] == 0
    for _ in range(2):
        assert chief("loop", "set", str(made["id"]), "--evidence", f"built={json.dumps({'name': 'second.tgz', 'sha256': 'b' * 64})}")[0] == 0
    run.step("evidence dedupe: on an open run the same row is added once however many times the writer repeats it",
             len([e for e in rows(part_key, live) if e["kind"] == "artifact_built"]) == 1, json.dumps(kinds(rows(part_key, live))))
