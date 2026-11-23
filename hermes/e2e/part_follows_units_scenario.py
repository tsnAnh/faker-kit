from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, DECISIONS, FOOTER, PROJECT, Board, commit_of, head_of, json_of, ledger

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

OTHER = "other-shop"
OTHER_BOT = "other-bot"
OTHER_REPO = "acme/other-shop"


def part_follows_units_scenario(run: Run) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n  {OTHER_BOT}:\n    role: project\n    project: {OTHER}\n    repo: {OTHER_REPO}\n", 1))
    board = Board(run)
    ledger(run)

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def part_card(ident: int | str) -> dict:
        return board.by_key()[f"faker-kit:chief:{ident}"]

    def pull(n: int, merged: bool) -> None:
        extra = {"state": "closed", "merged_at": "2026-10-10T00:00:00Z", "merge_commit_sha": commit_of(n)} if merged else {}
        run.update_gh(lambda state: state["pulls"].append({**run.pull(n, head_of(n)), "body": f"Change {n}", **extra}))

    def merge_pull(n: int) -> None:
        def change(state: dict) -> None:
            next(p for p in state["pulls"] if p["number"] == n).update(state="closed", merged_at="2026-10-10T00:00:00Z", merge_commit_sha=commit_of(n))
        run.update_gh(change)

    def make_part(text: str, *extra: str) -> str:
        return str(json_of(chief("handoff", "--project", PROJECT, "--part", text, "--title", text, "--decisions", DECISIONS, *extra))["id"])

    def unit(name: str, part: str, n: int | None = None) -> None:
        assert project("loop", "unit", name, "--title", f"Unit {name} lands", "--part", part, *(["--pr", str(n)] if n else []))[0] == 0

    def part_state(ident: str) -> str:
        return next((line.partition(": ")[2] for line in part_card(ident)["body"].splitlines() if line.startswith("State: ")), "")

    def part_prs(ident: str) -> str:
        return next((line.partition(": ")[2] for line in part_card(ident)["body"].splitlines() if line.startswith("PRs: ")), "")

    def brief(out: dict) -> str:
        wanted = ("merged", "waiting_on_units", "awaiting_stage_evidence", "parts_without_pr", "refused", "completion_pending")
        return json.dumps({k: v for k, v in out["entries"].items() if k in wanted and v})[:400]

    def waiting(out: dict) -> dict[str, list[str]]:
        return {r["id"]: r["units"] for r in out["entries"]["waiting_on_units"]}

    migrated = [chief("loop", "migrate", "--apply"), project("loop", "migrate", "--apply")]
    assert all(m[0] == 0 for m in migrated), [m[2] for m in migrated]

    single = make_part("Visitors search the catalog")
    pull(701, True)
    unit("search-box", single, 701)
    mark = len(board.verbs())
    early = json_of(chief("loop", "reconcile"))
    run.step("part follows units 1: a part with no pull request of its own and one unit whose pull request merged waits while the unit card is open, and says so under waiting_on_units",
             waiting(early).get(single) == [board.by_key()[key("search-box")]["id"]] and part_state(single) == "handed-out" and part_prs(single) == "",
             brief(early))
    project_pass = json_of(project("loop", "reconcile"))
    run.clock += 7 * 3600
    after_unit = json_of(chief("loop", "reconcile"))
    run.step("part follows units 1: once the unit card is settled the part becomes merged by itself, records the unit's pull request in PRs, and Kanban completes it through the normal path",
             "search-box" in project_pass["entries"]["merged"] and single in after_unit["entries"]["merged"] and part_state(single) == "merged" and part_prs(single) == "701"
             and part_card(single)["status"] == "done", brief(after_unit) + part_state(single) + part_prs(single))
    mark = len(board.verbs())
    again = json_of(chief("loop", "reconcile"))
    run.step("part follows units 1: a second reconcile changes nothing",
             single not in again["entries"]["merged"] and part_state(single) == "merged"
             and not [v for v in board.verbs()[mark:] if v[0] in ("edit", "complete", "claim", "schedule")],
             json.dumps(board.verbs()[mark:])[:300])

    pair = make_part("Visitors filter the catalog")
    pull(702, True)
    pull(703, False)
    unit("filter-bar", pair, 702)
    unit("filter-menu", pair, 703)
    json_of(project("loop", "reconcile"))
    run.clock += 7 * 3600
    stay = json_of(chief("loop", "reconcile"))
    menu_id = board.by_key()[key("filter-menu")]["id"]
    run.step("part follows units 2: a part with one merged unit and one unit whose pull request is open stays, and waiting_on_units names the open unit",
             part_state(pair) == "handed-out" and waiting(stay).get(pair) == [menu_id] and pair not in stay["entries"]["merged"] and part_prs(pair) == "",
             brief(stay))
    merge_pull(703)
    json_of(project("loop", "reconcile"))
    run.clock += 7 * 3600
    moved = json_of(chief("loop", "reconcile"))
    run.step("part follows units 2: when the second unit's pull request merges the part moves and records both pull requests",
             pair in moved["entries"]["merged"] and part_state(pair) == "merged" and part_prs(pair) == "702,703" and part_card(pair)["status"] == "done",
             brief(moved) + part_prs(pair))

    mixed = make_part("Visitors sort the catalog")
    pull(704, True)
    unit("sort-old", mixed)
    unit("sort-new", mixed, 704)
    assert project("loop", "close", "sort-old", "--as", "superseded", "--reason", "sort-new replaced it")[0] == 0
    json_of(project("loop", "reconcile"))
    run.clock += 7 * 3600
    carried = json_of(chief("loop", "reconcile"))
    run.step("part follows units 2: a unit closed as abandoned does not block while the merged unit carries the pull request: the part is merged in the kit with that pull request "
             "and, because the fake Kanban still holds the archived unit as an open prerequisite, its completion is reported as pending and retried, never forced",
             mixed not in waiting(carried) and part_state(mixed) == "merged" and part_prs(mixed) == "704" and part_card(mixed)["status"] != "done"
             and mixed in {r["id"] for r in carried["entries"]["completion_pending"]}, brief(carried) + part_prs(mixed))

    bare = make_part("Visitors share the catalog")
    unit("share-link", bare)
    assert project("loop", "close", "share-link", "--as", "superseded", "--reason", "nothing to ship")[0] == 0
    run.clock += 7 * 3600
    mark = len(board.verbs())
    first = json_of(chief("loop", "reconcile"))
    listed = {r["id"]: r for r in first["entries"]["parts_without_pr"]}
    second = json_of(chief("loop", "reconcile"))
    run.step("part follows units 3: a part whose units are all closed and carry no pull request is reported once under parts_without_pr with its unit refs and states, "
             "never merged, and not reported again",
             bare in listed and [u["state"] for u in listed[bare]["units"]] == ["abandoned"] and part_state(bare) == "handed-out" and part_card(bare)["status"] != "done"
             and bare not in first["entries"]["merged"] and not second["entries"]["parts_without_pr"] and first["parts_without_pr"] == 1 and second["parts_without_pr"] == 0
             and not [v for v in board.verbs()[mark:] if v[0] in ("complete", "archive")],
             json.dumps({"first": first["entries"]["parts_without_pr"], "second": second["entries"]["parts_without_pr"]}))

    own = make_part("Visitors reset the catalog")
    pull(710, True)
    assert chief("loop", "set", own, "--state", "in-progress", "--pr", "710")[0] == 0
    run.clock += 7 * 3600
    own_out = json_of(chief("loop", "reconcile"))
    run.step("part follows units 4: a part with its own pull request and no units settles exactly as before",
             own in own_out["entries"]["merged"] and part_state(own) == "merged" and part_prs(own) == "710" and own not in waiting(own_out), brief(own_out))

    installed = make_part("Visitors install the catalog app", "--outcome", "installed")
    pull(711, True)
    unit("install-app", installed, 711)
    json_of(project("loop", "reconcile"))
    run.clock += 7 * 3600
    stage = json_of(chief("loop", "reconcile"))
    run.step("part follows units 4: an installed part whose unit pull request merged waits for its stage evidence and is not merged",
             {"id": installed, "outcome": "installed"} in stage["entries"]["awaiting_stage_evidence"] and part_state(installed) == "handed-out" and installed not in stage["entries"]["merged"],
             brief(stage))

    far = str(json_of(chief("handoff", "--project", OTHER, "--part", "Other shop part", "--title", "Other shop part", "--decisions", DECISIONS))["id"])
    near = make_part("Visitors rate the catalog")
    pull(721, True)

    def far_repo(state: dict) -> None:
        alt = copy.deepcopy({k: v for k, v in state.items() if k != "alt"})
        alt["repo"] = OTHER_REPO
        alt["pulls"] = [{**run.pull(720, head_of(720)), "body": "Change 720", "state": "closed", "merged_at": "2026-10-10T00:00:00Z", "merge_commit_sha": commit_of(720)}]
        state["alt"] = {OTHER_REPO: alt}

    run.update_gh(far_repo)
    far_task = part_card(far)["id"]
    unit_body = "\n".join([FOOTER, f"Key: faker-kit:{OTHER}:rate-far", "Kind: unit", f"Project: {OTHER}", f"Bot: {OTHER_BOT}", "Outcome: merged-pr", "State: merged",
                           "PRs: 720", f"Part: {far}", "Ref: OT-1", "Added: 1.0", "Updated: 1.0"])
    far_unit = board.add_card(f"faker-kit:{OTHER}:rate-far", "OT-1 rate-far", unit_body, assignee=OTHER_BOT, status="done")
    data = board.state()
    data["links"].append([far_unit, far_task])
    board.path.write_text(json.dumps(data))
    unit("rate-near", near, 721)
    project_out = json_of(project("loop", "reconcile"))
    after_project = (part_state(near), part_state(far))
    run.clock += 7 * 3600
    everywhere = json_of(chief("loop", "reconcile"))
    run.step("part follows units 5: a project bot's reconcile settles its own unit cards and never a part, and the chief's reconcile covers the parts of every project",
             "rate-near" in project_out["entries"]["merged"] and after_project == ("handed-out", "handed-out") and near in everywhere["entries"]["merged"] and far in everywhere["entries"]["merged"]
             and part_state(near) == "merged" and part_state(far) == "merged", json.dumps({"after_project": after_project, "chief": everywhere["entries"]["merged"]}))
