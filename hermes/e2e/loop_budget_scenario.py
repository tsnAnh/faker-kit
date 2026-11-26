from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, PROJECT, Board, commit_of, head_of, json_of, ledger
from kanban_visibility_scenario import part_body, unit_body

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

CALL_SECONDS = 0.15
CLOSED_UNITS = 100
PLAIN_UNITS = 150
MERGED_UNITS = 20
OPEN_PR_UNITS = 10
OPEN_PARTS = 30
MERGED_PARTS = 10


class Meter:
    def __init__(self) -> None:
        self.seconds = 0.0
        self.kanban: Counter[str] = Counter()
        self.github = 0

    def now(self) -> float:
        return self.seconds

    def reset(self) -> None:
        self.seconds, self.kanban, self.github = 0.0, Counter(), 0

    @property
    def calls(self) -> int:
        return sum(self.kanban.values())

    def kanban_runner(self, bot: str):  # noqa: ANN201
        from faker_kit_kanban import hermes_runner
        inner = hermes_runner(bot)

        def run(args: list[str], stdin: str | None) -> str:
            self.seconds += CALL_SECONDS
            self.kanban[" ".join(args[:2]) if args[:1] == ["evidence"] else args[0]] += 1
            return inner(args, stdin)
        return run

    def gh(self, args: list[str], input: str | None = None) -> str:  # noqa: A002
        from faker_kit_gh import run_gh
        self.seconds += CALL_SECONDS
        self.github += 1
        return run_gh(args, input)

    def wiring(self) -> dict:
        import inspect

        import faker_kit
        extra = {"monotonic": self.now} if "monotonic" in inspect.signature(faker_kit.main).parameters else {}
        return {"kanban_runner": self.kanban_runner, "run": self.gh, **extra}

    def line(self) -> str:
        return f"{self.calls} Kanban calls {dict(sorted(self.kanban.items()))}, {self.github} GitHub calls, {self.seconds:.2f}s modelled"


def build_board(run: Run) -> Board:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)
    assert run.kit("loop", "migrate", "--apply", bot=CHIEF)[0] == 0
    assert run.kit("loop", "migrate", "--apply", bot=BOT)[0] == 0
    pulls: list[dict] = []
    merged = {"state": "closed", "merged_at": "2026-10-10T00:00:00Z"}
    numbers = iter(range(1001, 2000))

    def pull(n: int, unit: str | None, settled: bool) -> None:
        body = f"Unit: {unit}\n\nChange for {unit}" if unit else "A change with no unit line"
        pulls.append({**run.pull(n, head_of(n)), "body": body, **({**merged, "merge_commit_sha": commit_of(n)} if settled else {})})

    def unit(name: str, number: int | None = None, settled: bool = False, status: str = "ready", state: str = "in-progress") -> None:
        body = unit_body(name).replace("State: in-progress", f"State: {state}") + (f"\nPRs: {number}" if number else "")
        if number and state == "in-progress":
            pull(number, name, settled)
        board.add_card(f"faker-kit:{PROJECT}:{name}", name, body, status=status)

    for i in range(CLOSED_UNITS):
        unit(f"old-{i}", next(numbers), status="done", state="merged")
    for i in range(MERGED_UNITS // 2):
        unit(f"landed-{i}", next(numbers), True)
    for i in range(PLAIN_UNITS):
        unit(f"plain-{i}")
    for i in range(MERGED_UNITS // 2, MERGED_UNITS):
        unit(f"landed-{i}", next(numbers), True)
    for i in range(OPEN_PR_UNITS):
        unit(f"live-{i}", next(numbers))
    for i in range(OPEN_PARTS):
        ident, landed = 100 + i, i < MERGED_PARTS
        number = next(numbers)
        if landed:
            pull(number, None, True)
        board.add_card(f"faker-kit:chief:{ident}", f"part-{ident}", part_body(ident, "in-progress", prs=str(number) if landed else ""))
    run.update_gh(lambda state: state["pulls"].extend(pulls))
    return board


def loop_budget_scenario(run: Run) -> None:
    board = build_board(run)
    meter = Meter()
    cards = len(board.state()["tasks"])
    ids = {key.rsplit(":", 1)[-1]: task["id"] for key, task in board.by_key().items()}

    def one_pass(bot: str, *argv: str, env: dict[str, str] | None = None) -> dict:
        meter.reset()
        mark = len(board.calls())
        code, out, err = run.kit("loop", *argv, bot=bot, wiring=meter.wiring(), env=env)
        return {"code": code, "out": json.loads(out) if out.strip() else {}, "err": err, "seconds": meter.seconds, "calls": meter.calls, "line": meter.line(),
                "made": [(verb, call) for verb, call in ((call[2:][0] if call[:1] == ["--board"] else call[0], call) for _, call in board.calls()[mark:])]}

    def drain(bot: str, *argv: str, limit: int = 6) -> list[dict]:
        passes: list[dict] = []
        while len(passes) < limit:
            passes.append(one_pass(bot, *argv))
            if not passes[-1]["out"].get("partial"):
                break
        return passes

    def evidence_of(done: dict) -> list[str]:
        return [call[call.index("list") + 1] for verb, call in done["made"] if verb == "evidence" and "list" in call]

    landed = {ids[f"landed-{i}"] for i in range(MERGED_UNITS)}
    first_wave = {ids[f"landed-{i}"] for i in range(MERGED_UNITS // 2)}

    def settled() -> set[str]:
        return {t["id"] for t in board.state()["tasks"] if t["id"] in landed and t["status"] == "done"}

    one = one_pass(BOT, "reconcile")
    first = one["out"]
    run.step(f"reconcile on {cards} cards at {CALL_SECONDS}s a call: the first pass stops at its wall budget with exit 0, says partial, lists the cards it did not reach, and keeps what it settled "
             f"({one['line']})",
             one["code"] == 0 and first["partial"] is True and 0 < first["reached"] < first["candidates"] and first["unreached"] and one["seconds"] <= 30
             and first["set_merged"] == len(first_wave) == len(settled()) and settled() == first_wave, one["line"])
    cursor = run.team / "passes" / BOT / "reconcile.json"
    marks = json.loads(cursor.read_text())
    run.step("the cursor is a small private file in the kit's own store holding only card ids and the times they were last reached, and every card the pass reached is in it",
             (cursor.stat().st_mode & 0o777) == 0o600 and len(marks) == first["reached"] and not set(marks) & set(first["unreached"]) and not any(".tmp" in p.name for p in cursor.parent.iterdir()),
             json.dumps(first["unreached"][:3]))
    second = one_pass(BOT, "reconcile")
    reached_first = set(marks)
    listed = list(dict.fromkeys(evidence_of(second)))
    head = listed[: len(first["unreached"])]
    touched = [verb for verb, call in second["made"] if first_wave & set(call)]
    run.step("the next pass resumes from the cursor: the cards the first pass did not reach are read first, in order, before any card it already reached",
             second["code"] == 0 and head == [i for i in first["unreached"] if i in listed][: len(head)] and not reached_first & set(head), f"{len(head)} cards read first, {len(reached_first & set(head))} of them reached by the first pass")
    run.step("the next pass never touches a card the first pass merged, and settles more of the board inside its own budget",
             not touched and second["out"]["set_merged"] > 0 and second["seconds"] <= 30, f"{touched} {second['line']}")
    rest: list[dict] = []
    while len(rest) < 4:
        rest.append(one_pass(BOT, "reconcile"))
        if len(json.loads(cursor.read_text())) >= rest[-1]["out"]["candidates"]:
            break
    done_runs = [c["task"] for c in board.state()["completions"]]
    run.step("the remaining passes reach every card: each pass stays under 30 s, no card is completed twice, every merged pull request's card is done, and the cursor holds every open card",
             all(p["code"] == 0 and p["seconds"] <= 30 for p in rest) and sorted(done_runs) == sorted(landed) and settled() == landed
             and len(json.loads(cursor.read_text())) >= rest[-1]["out"]["candidates"] and len(rest) <= 3,
             f"{len(rest)} passes, {len(done_runs)}/{len(landed)} done " + " | ".join(f"r{p['out']['reached']}/c{p['out']['candidates']} {p['seconds']:.1f}s" for p in rest))

    def cover(bot: str, kind: str, *argv: str, limit: int = 4, env: dict[str, str] | None = None) -> list[dict]:
        found: list[dict] = []
        path = run.team / "passes" / bot / f"{kind}.json"
        while len(found) < limit:
            found.append(one_pass(bot, *argv, env=env))
            if len(json.loads(path.read_text())) >= found[-1]["out"]["candidates"]:
                break
        return found

    for who, bot, seconds in (("project", BOT, "10"), ("chief", CHIEF, "25")):
        passes = cover(bot, "check", "check", env={"FAKER_KIT_PASS_BUDGET_SECONDS": seconds})
        shows = sum(p["calls"] for p in passes)
        run.step(f"{who} check on {cards} cards with a {seconds} s budget: the first pass stops at it and says partial, every pass stays under 30 s, and the passes together reach every card",
                 passes[0]["out"]["partial"] is True and passes[0]["out"]["ok"] is False and passes[0]["out"]["unreached"] and all(p["code"] in (0, 4) and p["seconds"] <= 30 for p in passes)
                 and len(passes) <= 3, f"{len(passes)} passes, {shows} Kanban calls " + " | ".join(f"r{p['out']['reached']}/c{p['out']['candidates']} {p['seconds']:.1f}s" for p in passes))

    tiny = {"FAKER_KIT_PASS_BUDGET_SECONDS": "0.1"}
    nothing = one_pass(BOT, "check", env=tiny)
    run.step("loop check that ran out of budget before it read any card reports partial with nothing needing action: exit 0, not ok, no finding, every card listed as not reached, never a clean result",
             nothing["code"] == 0 and nothing["out"]["partial"] is True and nothing["out"]["ok"] is False and nothing["out"]["needs_action"] is False and nothing["out"]["findings"] == []
             and nothing["out"]["reached"] == 0 and len(nothing["out"]["unreached"]) == nothing["out"]["candidates"], nothing["line"])

    cut = one_pass(CHIEF, "digest", env=tiny)
    full = one_pass(CHIEF, "digest", env={"FAKER_KIT_PASS_BUDGET_SECONDS": "1000"})
    run.step("loop digest labels itself partial when its reconcile or its check ran out of budget, and not partial when both finished",
             cut["code"] == 0 and cut["out"]["partial"] is True and cut["out"]["reconcile"]["partial"] is True and cut["out"]["check_unreached"] and "closed" in cut["out"]
             and full["code"] == 0 and full["out"]["partial"] is False and full["out"]["check_unreached"] == [], f"cut: {cut['line']}; full: {full['line']}")

    chief = cover(CHIEF, "reconcile", "reconcile")
    digest = one_pass(CHIEF, "digest")
    run.step(f"chief reconcile and digest on {cards} cards stay under 30 s and the digest says whether it is partial",
             all(p["code"] == 0 and p["seconds"] <= 30 for p in chief) and digest["code"] == 0 and digest["seconds"] <= 30 and isinstance(digest["out"]["partial"], bool), digest["line"])
