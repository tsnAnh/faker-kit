from __future__ import annotations

import json
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, PROJECT, REPO, Board, commit_of, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

DECISIONS = "none: bug fix, expected behaviour obvious from the report"
BUILT = json.dumps({"name": "release.tgz", "sha256": "a" * 64})


def claims_scenario(run: Run) -> None:
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

    def merged_pr(n: int, unit: str | None) -> None:
        body = f"Unit: {unit}\n\nChange for {unit}" if unit else "A change"
        run.update_gh(lambda state: state["pulls"].append({**run.pull(n, head_of(n)), "body": body, "state": "closed", "merged_at": "2026-10-10T00:00:00Z", "merge_commit_sha": commit_of(n)}))

    def rows(card_key: str, run_id: int | None = None) -> list[dict]:
        card = board.by_key()[card_key]
        return [e for e in board.state()["evidence"] if e["task_id"] == card["id"] and (run_id is None or e["run_id"] == run_id)]

    def runs(card_key: str) -> list[dict]:
        card = board.by_key()[card_key]
        return [r for r in board.state()["runs"] if r["task_id"] == card["id"]]

    assert chief("loop", "migrate", "--apply")[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0

    assert project("loop", "unit", "later-a", "--title", "Visitors see the fixture change")[0] == 0
    merged_pr(71, "later-a")
    existing = project("loop", "unit", "later-a", "--title", "Visitors see the fixture change", "--pr", "71")
    again = project("loop", "unit", "later-a", "--title", "Visitors see the fixture change", "--pr", "71")
    mutations = board.mutations()
    bound = project("loop", "unit", "later-a", "--title", "Visitors see the fixture change", "--pr", "71")
    run.step("kanban claims: loop unit <id> --pr <n> on an existing card binds the number into its PRs field once and a repeat edits nothing",
             existing[0] == 0 and "PRs: 71" in board.body(key("later-a")) and again[0] == 0 and bound[0] == 0 and board.mutations() == mutations, existing[2].strip())

    board.set(complete_refuse="pending")
    first = json_of(project("loop", "reconcile"))
    refused = {r["id"]: r for r in first["entries"]["refused"]}
    later = board.by_key()[key("later-a")]
    run.step("kanban claims: a merged pull request whose acceptance refuses (pending) is reported once, the card stays ready with no claim left open, and it never reaches blocked",
             refused["later-a"]["classification"] == "pending" and later["status"] == "ready" and board.open_claims(key("later-a")) == [] and len(runs(key("later-a"))) == 1
             and runs(key("later-a"))[0]["status"] == "reclaimed" and board.state()["failures"].get(later["id"], 0) == 0 and "claim_left_open" not in refused["later-a"],
             json.dumps(refused["later-a"]))

    board.set(complete_refuse="text-infra")
    run.clock += 1000
    infra = json_of(project("loop", "reconcile"))
    infra_row = {r["id"]: r for r in infra["entries"]["refused"]}["later-a"]
    board.set(complete_refuse=None)
    run.clock += 1000
    done = json_of(project("loop", "reconcile"))
    later = board.by_key()[key("later-a")]
    run.step("kanban claims: after a pending and an infra refusal the next pass completes the card; evidence rows exist for the completing run and at most one claim was ever open",
             infra_row["classification"] == "infra" and "later-a" in done["entries"]["merged"] and later["status"] == "done" and board.open_claims(key("later-a")) == []
             and len(runs(key("later-a"))) == 3 and sorted(e["kind"] for e in rows(key("later-a"), 3)) == ["pr_merged", "pr_published"]
             and rows(key("later-a"), 3)[1]["payload"]["merge_commit"] == commit_of(71) and board.state()["peak"][later["id"]] == 1 and "State: merged" in board.body(key("later-a")),
             json.dumps(done["entries"]["merged"]))

    def card_for(unit: str, n: int) -> None:
        merged_pr(n, unit)
        assert project("loop", "unit", unit, "--title", "Visitors see the fixture change", "--pr", str(n))[0] == 0

    card_for("moved-b", 72)
    old = board.leave_claim(key("moved-b"), "pr_published", "pr_merged", url=url(72), head=head_of(72), commit=commit_of(72))
    board.stale_claim(key("moved-b"))
    moved = board.by_key()[key("moved-b")]
    before = len(rows(key("moved-b"), old))
    healed = json_of(project("loop", "reconcile"))
    run.step("kanban claims: evidence recorded on an older run that went stale is not copied; the next run gets its own pr_published and pr_merged from GitHub and the card completes",
             moved["status"] == "ready" and "moved-b" in healed["entries"]["merged"] and board.by_key()[key("moved-b")]["status"] == "done"
             and len(rows(key("moved-b"), old)) == before == 2 and sorted(e["kind"] for e in rows(key("moved-b"), old + 1)) == ["pr_merged", "pr_published"]
             and board.open_claims(key("moved-b")) == [], json.dumps(healed["entries"]["merged"]))

    card_for("gaveup-d", 73)
    first_run = board.leave_claim(key("gaveup-d"), "pr_published", url=url(73), head=head_of(73), commit=commit_of(73))
    board.stale_claim(key("gaveup-d"))
    board.leave_claim(key("gaveup-d"), url=url(73), head=head_of(73), commit=commit_of(73))
    board.stale_claim(key("gaveup-d"))
    gave = board.by_key()[key("gaveup-d")]
    before_state = board.state()
    mutations = board.mutations()
    verbs_before = len(board.calls())
    held = json_of(project("loop", "reconcile"))
    line = f"hermes kanban unblock {gave['id']}"
    stuck = {r["id"]: r for r in held["entries"]["stale_blocked"]}.get("gaveup-d", {})
    after_state = board.state()
    run.step("kanban claims: a card Kanban gave up on after a stale kit claim is reported stale_blocked with the exact unblock line and nothing is claimed, written or unblocked",
             gave["status"] == "blocked" and first_run > 0 and stuck.get("unblock") == line and stuck.get("card") == gave["id"] and held["stale_blocked"] == 1
                 and "gaveup-d" not in held["entries"]["merged"]
             and board.mutations() == mutations and board.by_key()[key("gaveup-d")]["status"] == "blocked" and after_state["runs"] == before_state["runs"]
             and after_state["evidence"] == before_state["evidence"]
             and not any(tok == "unblock" for _, rest in board.calls()[verbs_before:] for tok in rest), json.dumps(stuck))

    card_for("never-f", 74)
    board.set(complete_refuse="pending")
    for pause in (0, 1000, 4000):
        run.clock += pause
        project("loop", "reconcile")
    never = board.by_key()[key("never-f")]
    run.step("kanban claims: three refused attempts in a row, each after its hold, leave the card ready with no claim and no failure count, so Kanban never gives it up",
             never["status"] == "ready" and board.open_claims(key("never-f")) == [] and len(runs(key("never-f"))) == 3 and board.state()["failures"].get(never["id"], 0) == 0
             and board.state()["peak"][never["id"]] == 1, str(never["status"]))
    board.set(complete_refuse=None)
    run.clock += 15000
    assert project("loop", "reconcile")[0] == 0
    mutations, snapshot = board.mutations(), board.state()
    quiet = json_of(project("loop", "reconcile"))
    run.step("kanban claims: a pass after everything is completed changes nothing: no claim, no evidence, no edit",
             quiet["set_merged"] == 0 and board.mutations() == mutations and board.state()["evidence"] == snapshot["evidence"] and board.state()["runs"] == snapshot["runs"]
             and board.by_key()[key("never-f")]["status"] == "done", json.dumps(quiet)[:200])

    parts = {}
    for label, extra in (("plain", []), ("built", ["--outcome", "built"])):
        made = json_of(chief("handoff", "--project", PROJECT, "--part", f"Part {label} card", "--title", f"Part {label} card", "--decisions", DECISIONS, *extra))
        parts[label] = made["id"]
    base = 90
    for ident in parts.values():
        base += 1
        assert chief("loop", "set", str(ident), "--state", "in-progress", "--pr", str(base))[0] == 0
        merged_pr(base, None)
    built_key = f"faker-kit:chief:{parts['built']}"
    plain_key = f"faker-kit:chief:{parts['plain']}"
    recorded = chief("loop", "set", str(parts["built"]), "--evidence", f"built={BUILT}")
    run.step("kanban claims: recording stage evidence for a part claims, adds the evidence and releases the claim, so no claim is held until the pull request merges",
             recorded[0] == 0 and board.open_claims(built_key) == [] and [e["kind"] for e in rows(built_key)] == ["artifact_built"] and board.by_key()[built_key]["status"] == "ready",
             recorded[2].strip())
    old = board.leave_claim(plain_key, "pr_published", "pr_merged", url=url(91), head=head_of(91), commit=commit_of(91))
    board.stale_claim(plain_key)
    finished = json_of(chief("loop", "reconcile"))
    plain_done = board.by_key()[plain_key]
    built_done = board.by_key()[built_key]
    built_runs = sorted({e["run_id"] for e in rows(built_key)})
    last_built = [e["kind"] for e in rows(built_key, built_runs[-1])]
    run.step("kanban claims: the chief's part cards complete the same way: evidence from a stale older run is not reused, and a built part gets its stage evidence again on the run that completes",
             str(parts["plain"]) in finished["entries"]["merged"] and plain_done["status"] == "done" and sorted(e["kind"] for e in rows(plain_key, old + 1)) == ["pr_merged", "pr_published"]
             and str(parts["built"]) in finished["entries"]["merged"] and built_done["status"] == "done" and len(built_runs) == 2 and sorted(last_built) == ["artifact_built", "pr_published"]
             and board.open_claims(plain_key) == [] and board.open_claims(built_key) == [], json.dumps(finished["entries"]["merged"]))

    flat = {v[0] for v in board.verbs()}
    tokens = {token for _, rest in board.calls() for token in rest}
    run.step("kanban claims: the only new Kanban call is reclaim, used to release the kit's own claim; no unblock, force, or dispatch call appears",
             "reclaim" in flat and not tokens & {"unblock", "--force", "dispatch", "daemon", "swarm", "specify", "decompose", "set-dispatch-mode"}, ",".join(sorted(flat)))
