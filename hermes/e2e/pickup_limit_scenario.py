from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

from kanban_scenario import BOT, PROJECT, Board, commit_of, head_of, json_of
from pickup_scenario import card_body, cron

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

SESSION = "https://claude.ai/code/session_01E2Eabc"
GOAL = "Add a keyboard shortcut for the search box and cover it in the shop tests"
DECISIONS = "shortcut is slash; no settings page"
UNITS = 50


def pickup_limit_scenario(run: Run) -> None:
    guard = run.team / "guard.yaml"
    board = Board(run)
    (run.team / "kanban-cutover.json").write_text(json.dumps({"at": run.clock, "board": "default", "parts": [], "units": {}}))

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT, tool=cron)

    def machine(per_project: int | None, total: int) -> None:
        text = re.sub(r"^max_in_flight_(total|per_project):.*\n", "", guard.read_text(), flags=re.M)
        lines = [f"max_in_flight_total: {total}"] + ([f"max_in_flight_per_project: {per_project}"] if per_project is not None else [])
        guard.write_text(text.replace("version: 2\n", "version: 2\n" + "\n".join(lines) + "\n", 1))

    def repository(limit: int | None) -> None:
        run.update_gh(lambda st: st["files"].__setitem__(".agents/dispatch.json", json.dumps({"max_in_flight": limit} if limit is not None else {"forbidden_paths": ["infra/"]})))

    def key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def add(unit: str, status: str = "ready", added: float = 5.0) -> str:
        return board.add_card(key(unit), unit, card_body(unit, GOAL, DECISIONS, added), BOT, status)

    def launch(unit: str) -> tuple[int, str, str]:
        return project("launch", "--brief-file", str(run.brief(f"{unit}.md", f"/faker-mode\nbuild {unit}\n")))

    def claimed(result: tuple[int, str, str]) -> bool:
        return result[0] == 0 and json_of(result).get("card", {}).get("claim") == "claimed"

    def finish(units: list[str]) -> None:
        for unit in units:
            board.edit_card(key(unit), status="done")

    run.step("pickup per-project limit: the project bot may turn pickup on only through the owner, and the fixture starts with no card running",
             run.kit("pickup", "on", bot=None, tool=cron)[0] == 0 and json_of(project("pickup", "status")).get("running_total") == 0, "pickup on")

    repository(1)
    machine(50, 50)
    names = [f"u-{i}" for i in range(1, UNITS + 2)]
    for index, name in enumerate(names):
        add(name, added=float(index))
    status = json_of(project("pickup", "status"))
    run.step("pickup per-project limit: a repository that says max_in_flight 1 and a machine that says 50 per project (total 50) print 50 with source machine and 50 free slots, "
             "and the gate lists a line for each of 50 candidates",
             status.get("per_project") == 50 and status.get("per_project_source") == "machine" and status.get("total") == 50 and status.get("free_slots") == 50 and len(status.get("pickup", [])) == 50,
             json.dumps({k: status.get(k) for k in ("per_project", "per_project_source", "total", "free_slots")}))
    results = [launch(name) for name in names[:UNITS]]
    started = [claimed(result) for result in results]
    noticed = [index + 1 for index, result in enumerate(results) if json_of(result).get("notice")]
    run.step("pickup per-project limit: launch prints a soft notice naming the running count from the 26th running unit of a project on, and none before", noticed == list(range(26, UNITS + 1))
             and "26 unit cards of shop are running" in json_of(results[25])["notice"], json.dumps(noticed[:3]))
    run.step("pickup per-project limit: with the repository at 1, units 1 to 50 each launch and claim their card under the machine's 50",
             all(started) and len(started) == UNITS, f"{sum(started)} of {UNITS} launched")
    full = json_of(project("pickup", "status"))
    fifty_first = launch(names[UNITS])
    run.step("pickup per-project limit: the 51st launch is refused naming 50 of 50 unit cards and 50 of 50 in total, claims nothing and lists no candidate",
             fifty_first[0] != 0 and "50 of 50 unit cards" in fifty_first[2] and "50 of 50 in total" in fifty_first[2] and "free slots: 0" in fifty_first[2]
             and board.by_key()[key(names[UNITS])]["status"] == "ready" and full.get("pickup") == [] and full.get("running") == 50, fifty_first[2].strip()[:200])

    machine(50, 500)
    roomy = launch(names[UNITS])
    run.step("pickup per-project limit: with a total of 500 the 51st launch is still refused by the 50 per project, naming 50 of 50 unit cards and 50 of 500 in total",
             roomy[0] != 0 and "50 of 50 unit cards" in roomy[2] and "50 of 500 in total" in roomy[2], roomy[2].strip()[:200])
    finish(names[:UNITS])

    machine(2, 500)
    repository(10)
    add("run-a", "running")
    add("run-b", "running")
    lowered = json_of(project("pickup", "status"))
    low = launch(names[UNITS])
    run.step("pickup per-project limit: a machine value of 2 under a repository value of 10 lowers the limit to 2, prints source machine, and refuses a third running unit naming 2 of 2",
             lowered.get("per_project") == 2 and lowered.get("per_project_source") == "machine" and low[0] != 0 and "2 of 2 unit cards" in low[2], low[2].strip()[:200])

    machine(None, 500)
    repository(3)
    from_repo = json_of(project("pickup", "status"))
    third = launch(names[UNITS])
    run.step("pickup per-project limit: a guard.yaml with no per-project line falls back to the repository's 3 (source repository), and the third running unit launches",
             from_repo.get("per_project") == 3 and from_repo.get("per_project_source") == "repository" and claimed(third), third[2].strip()[:200])
    add("w-extra")
    fourth = launch("w-extra")
    run.step("pickup per-project limit: the repository's 3 is enforced once reached, naming 3 of 3 unit cards", fourth[0] != 0 and "3 of 3 unit cards" in fourth[2], fourth[2].strip()[:200])

    repository(None)
    fallback = json_of(project("pickup", "status"))
    run.step("pickup per-project limit: with no machine value and no repository value the limit is the default 50 and the source is default",
             fallback.get("per_project") == 50 and fallback.get("per_project_source") == "default" and fallback.get("total") == 500,
             json.dumps({k: fallback.get(k) for k in ("per_project", "per_project_source", "total")}))

    big = (run.team / "guard.yaml").read_text().replace("max_in_flight_total: 500", "max_in_flight_total: 1000")
    guard.write_text(big)
    run.step("pickup per-project limit: a guard.yaml total of 1000 is read and printed", json_of(project("pickup", "status")).get("total") == 1000, "1000")


    finish(["run-a", "run-b", "w-extra", names[UNITS]])
    for index in range(UNITS):
        unit, number = f"p-{index}", 300 + index
        run.update_gh(lambda st, unit=unit, number=number: st["pulls"].append({**run.pull(number, head_of(number)), "body": f"Unit: {unit}\n\nChange for {unit}"}))
        board.add_card(key(unit), unit, card_body(unit, GOAL, DECISIONS, 100.0 + index, fields={"PRs": str(number)}, repeated=(("Session", SESSION),)), BOT, "ready")
        board.leave_claim(key(unit), url=f"https://github.com/acme/shop/pull/{number}", head=head_of(number), commit=commit_of(number))
    machine(50, 500)
    shown = project("status")
    covered = {number for number in range(300, 300 + UNITS) if any(f"#{number}" in line for line in shown[1].splitlines())}
    run.step("pickup per-project limit: faker-kit status reports every one of 50 open unit pull requests, not the first 30 only",
             shown[0] == 0 and len(covered) == UNITS, f"{len(covered)} of {UNITS} pull requests have a status line")
    began = time.perf_counter()
    settled = project("loop", "reconcile")
    elapsed = time.perf_counter() - began
    run.step("pickup per-project limit: one loop reconcile over 50 running unit cards, each with an open pull request, finishes and records no refusal",
             settled[0] == 0 and not json_of(settled).get("unreadable"), f"{elapsed:.2f}s against the in-process fakes, not the real hermes or gh CLIs")
