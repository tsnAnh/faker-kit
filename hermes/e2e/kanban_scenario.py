from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

FAKE_KANBAN = r'''
def kanban_main():
    import os, sys
    state_path = root / "kanban-state.json"
    st = json.loads(state_path.read_text())
    rest = args[1:]
    if rest[:1] == ["--board"]:
        rest = rest[2:]
    verb, rest = rest[0], rest[1:]

    def save():
        state_path.write_text(json.dumps(st))

    def die(message, code=1):
        save()
        print(message, file=sys.stderr)
        sys.exit(code)

    def opts(tokens):
        found, positional, i = {}, [], 0
        while i < len(tokens):
            token = tokens[i]
            if token == "--":
                positional += tokens[i + 1:]
                break
            if token == "--json" and tokens[:1] != ["add"] and "--kind" not in tokens:
                found["--json"], i = True, i + 1
            elif token.startswith("--") and token not in ("--archived", "--force"):
                found[token], i = tokens[i + 1], i + 2
            elif token.startswith("--"):
                found[token], i = True, i + 1
            else:
                positional.append(token)
                i += 1
        return found, positional

    def task(ident):
        return next((t for t in st["tasks"] if t["id"] == ident), None)

    def parents_of(ident):
        return [p for p, c in st["links"] if c == ident]

    allowed = {"boards", "list", "show", "create", "link", "edit", "claim", "evidence", "complete", "archive"}
    if verb not in allowed or (verb == "boards" and rest[:1] != ["list"]):
        die(f"kanban: refused verb {verb}", 2)
    if verb == "boards":
        print(json.dumps([{"slug": "default", "dispatch_mode": st["dispatch_mode"], "archived": False}]))
        return
    reading = verb in ("list", "show") or (verb == "evidence" and rest[:1] == ["list"])
    if not reading:
        st["mutations"] += 1
        if st.get("fail_at") == st["mutations"]:
            st["fail_at"] = None
            die("database is locked")
    if verb == "list":
        shown = [t for t in st["tasks"] if "--archived" in rest or t["status"] != "archived"]
        print(json.dumps(shown))
    elif verb == "show":
        found = task(rest[0])
        if found is None:
            die("no such task")
        print(json.dumps({"task": found, "parents": parents_of(found["id"]), "children": [c for p, c in st["links"] if p == found["id"]], "comments": [], "events": [],
                          "runs": [r for r in st["runs"] if r["task_id"] == found["id"]], "latest_summary": None}))
    elif verb == "create":
        found, positional = opts(rest)
        key = found["--idempotency-key"]
        if "--force" in found or "--body-file" not in found or found["--body-file"] != "-":
            die("create needs --body-file -", 2)
        body = sys.stdin.read()
        existing = next((t for t in st["tasks"] if t["status"] != "archived" and t.get("idempotency") == key), None)
        if existing is None:
            if found.get("--completion-contract", "").startswith("fleet-ci:") and not found.get("--assignee"):
                die("fleet-ci needs an assignee")
            kind = json.loads(found["--outcome-contract"])["kind"]
            if kind not in ("merged_pr", "built_artifact", "installed_app", "other"):
                die("bad outcome contract")
            existing = {"id": f"t_{st['next']}", "title": positional[0], "body": body, "assignee": found.get("--assignee"), "status": "ready", "created_by": found.get("--created-by"),
                        "completion_contract": found.get("--completion-contract"), "outcome_contract": found["--outcome-contract"], "result": None, "idempotency": key}
            st["next"] += 1
            st["tasks"].append(existing)
            marker = Path(os.environ["FAKER_KIT_HOME"]) / "kanban-cutover.json"
            st["creates"].append({"key": key, "marker_present": marker.exists(), "profile": profile})
        shown = dict(existing)
        shown.pop("idempotency")
        print(json.dumps(shown))
    elif verb == "link":
        parent, child = rest[0], rest[1]
        if task(parent) is None or task(child) is None:
            die("no such task")
        if [parent, child] not in st["links"]:
            st["links"].append([parent, child])
    elif verb == "edit":
        found = task(rest[0])
        if found is None:
            die("no such task")
        found["body"] = rest[rest.index("--body") + 1]
    elif verb == "archive":
        found = task(rest[0])
        if found is None:
            die("no such task")
        found["status"] = "archived"
    elif verb == "claim":
        found = task(rest[0])
        if found is None or found["status"] != "ready" or found["assignee"] != profile:
            die("cannot claim: not ready for this profile")
        if any((task(p) or {}).get("status") != "done" for p in parents_of(found["id"])):
            die("cannot claim: prerequisites are not done")
        run_id = len(st["runs"]) + 1
        st["runs"].append({"id": run_id, "task_id": found["id"], "profile": profile, "started_at": 1, "ended_at": None})
        found["status"] = "running"
        print(json.dumps({"task_id": found["id"], "run_id": run_id, "profile": profile, "claim_expires": 2}))
    elif verb == "evidence" and rest[0] == "add":
        found, positional = opts(rest[1:])
        run_id = int(found["--run-id"])
        live = next((r for r in st["runs"] if r["id"] == run_id and r["task_id"] == positional[0] and r["ended_at"] is None), None)
        if live is None or st.get("evidence_refuse"):
            die("evidence refused: no live run" if live is None else "evidence refused: schema")
        st["evidence"].append({"id": len(st["evidence"]) + 1, "task_id": positional[0], "run_id": run_id, "kind": found["--kind"], "payload": json.loads(found["--json"])})
    elif verb == "evidence":
        print(json.dumps([e for e in st["evidence"] if e["task_id"] == rest[1]]))
    else:
        die("kanban: complete is not part of this scenario", 2)
    save()
'''

CHIEF = "ba-truong"
BOT = "meo-beo"
PROJECT = "shop"
REPO = "acme/shop"
DECISIONS = "none: bug fix, expected behaviour obvious from the report"
ALLOWED_VERBS = {"boards", "list", "show", "create", "link", "edit", "claim", "evidence", "archive"}
HEADER_KEYS = {"id", "project", "bot", "part", "decisions", "outcome", "stages", "reached", "state", "prs", "waits_on", "added", "updated", "reported"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Board:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.path = run.hermes / "kanban-state.json"
        self.path.write_text(json.dumps({"dispatch_mode": "external", "tasks": [], "links": [], "evidence": [], "runs": [], "mutations": 0, "fail_at": None, "next": 1, "creates": []}))

    def state(self) -> dict:
        return json.loads(self.path.read_text())

    def set(self, **changes: object) -> None:
        data = self.state()
        data.update(changes)
        self.path.write_text(json.dumps(data))

    def mutations(self) -> int:
        return int(self.state()["mutations"])

    def by_key(self) -> dict[str, dict]:
        return {line.partition(": ")[2]: t for t in self.state()["tasks"] for line in t["body"].splitlines() if line.startswith("Key: ")}

    def calls(self) -> list[tuple[str, list[str]]]:
        found = []
        for line in (self.run.hermes / "hermes-calls.log").read_text().splitlines():
            call = json.loads(line)
            if call[:1] == ["-p"]:
                profile, call = call[1], call[2:]
                if call[:1] == ["kanban"]:
                    found.append((profile, call[1:]))
        return found

    def verbs(self) -> list[tuple[str, ...]]:
        found = []
        for _, rest in self.calls():
            rest = rest[2:] if rest[:1] == ["--board"] else rest
            found.append(tuple(rest[:2]) if rest[0] in ("boards", "evidence") else (rest[0],))
        return found


def lines(task: dict) -> list[str]:
    return task["body"].partition("\n\n")[0].splitlines()


def ledger(run: Run) -> None:
    def part(ident: int, project: str, state: str, text: str, **extra: object) -> dict:
        return {"id": ident, "project": project, "bot": BOT if project == PROJECT else "gone-bot", "part": text, "decisions": "owner said so", "outcome": "merged-pr", "stages": {},
                "state": state, "prs": [], "waits_on": None, "added": run.clock - 5000 + ident, "updated": run.clock - 4000 + ident, "reported": None, **extra}

    parts = [part(1, PROJECT, "handed-out", "Add search to the catalog"),
             part(2, PROJECT, "in-progress", "Build the release archive", outcome="built", stages={"built": "run-17"}, prs=[11]),
             part(3, PROJECT, "waiting", "Wire the export button", waits_on="shop#9"),
             part(4, PROJECT, "blocked", "Rework the checkout page", reported="blocked"),
             part(5, PROJECT, "ready-to-merge", "Ship the csv export", prs=[12], reported="ready-to-merge"),
             part(6, PROJECT, "merged", "Old merged part", prs=[3]),
             part(7, PROJECT, "abandoned", "Old abandoned part"),
             part(8, PROJECT, "taken-back", "Old taken back part"),
             part(9, "gone", "in-progress", "A part for a project that left")]
    folder = run.team / "loop"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{CHIEF}.json").write_text(json.dumps({"parts": parts, "digest_at": None}))
    units = run.team / "briefs" / BOT / "orchestrate" / PROJECT
    units.mkdir(parents=True, exist_ok=True)
    rows = ["csv-export\trunning\tdispatch/csv\t12\t" + "a" * 40 + "\thttps://claude.ai/code/session_csv\tcsv.md\tsonnet\tmedium\tchief\t\tclaude",
            "login-fix\tawaiting-answers\t\t\t\t\tlogin.md\tsonnet\tmedium\towner\t\tclaude",
            "old-merged\tmerged\tb\t3\t" + "b" * 40, "old-closed\tclosed", "Bad Row\trunning", "lonely"]
    (units / "units.tsv").write_text("\n".join(rows) + "\n")


def json_of(result: tuple[int, str, str]) -> dict:
    return json.loads(result[1]) if result[0] == 0 and result[1].strip() else {}


def kanban_scenario(run: Run, loop_store: type, loop_error: type[Exception]) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)
    ledger_file = run.team / "loop" / f"{CHIEF}.json"
    marker = run.team / "kanban-cutover.json"
    ledger_hash = sha(ledger_file.read_text())

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    early = project("loop", "unit", "csv-export", "--part", "5")
    run.step("kanban: loop unit before the cutover is refused and creates nothing, since units still live in units.tsv",
             early[0] == 1 and "cutover is not done" in early[2] and board.mutations() == 0 and not board.state()["tasks"], early[2].strip())

    dry = chief("loop", "migrate", "--dry-run")
    shown = json_of(dry)
    run.step("kanban: the chief's migrate dry run counts open parts, skipped closed parts, and unmapped entries, makes no write call, and writes no marker",
             dry[0] == 0 and shown.get("mode") == "dry-run" and shown["board"] == {"slug": "default", "dispatch_mode": "external"} and shown["ledger_entries"] == 9 and shown["parts_open"] == 5
             and shown["parts_closed_skipped"] == {"merged": 1, "abandoned": 1, "taken-back": 1} and shown["cards_to_create"] == 5 and shown["cards_existing"] == 0 and shown["links_to_make"] == 0
             and [u["id"] for u in shown["unmapped"]] == ["9"] and "no longer on this machine" in shown["unmapped"][0]["reason"]
             and board.mutations() == 0 and not marker.exists() and set(board.verbs()) <= {("boards", "list"), ("list",)},
             json.dumps({k: shown.get(k) for k in ("parts_open", "cards_to_create", "unmapped")}))
    both = chief("loop", "migrate")
    neither = chief("loop", "migrate", "--dry-run", "--apply")
    run.step("kanban: migrate needs exactly one of --dry-run or --apply", both[0] == 1 and neither[0] == 1 and "exactly one" in both[2] and "exactly one" in neither[2], both[2].strip())
    pdry = json_of(project("loop", "migrate", "--dry-run"))
    run.step("kanban: a project bot's dry run counts open and closed units, the unit that serves a part, and unmapped rows, and writes nothing",
             pdry.get("units_open") == 2 and pdry["units_closed"] == 2 and pdry["cards_to_create"] == 2 and pdry["links_to_make"] == 1
             and sorted(u["id"] for u in pdry["unmapped"]) == ["Bad Row", "lonely"] and board.mutations() == 0 and not marker.exists()
             and not board.state()["tasks"], json.dumps(pdry.get("unmapped")))

    board.set(dispatch_mode="workers")
    refused = chief("loop", "migrate", "--apply")
    run.step("kanban: apply on a board that spawns workers is refused, and nothing is created or marked",
             refused[0] == 1 and "not an external board" in refused[2] and board.mutations() == 0 and not board.state()["tasks"] and not marker.exists(), refused[2].strip())
    board.set(dispatch_mode="external")
    early_project = project("loop", "migrate", "--apply")
    run.step("kanban: a project bot cannot apply before the chief has, since units link to part cards",
             early_project[0] == 1 and "chief has not applied" in early_project[2] and board.mutations() == 0 and not board.state()["tasks"], early_project[2].strip())

    board.set(fail_at=3)
    broken = chief("loop", "migrate", "--apply")
    run.step("kanban: an interrupted apply leaves the cards it made, no marker, and a refusal that says nothing more was done",
             broken[0] == 1 and "did not accept" in broken[2] and len(board.state()["tasks"]) == 2 and not marker.exists(), broken[2].strip())
    applied = chief("loop", "migrate", "--apply")
    done = json_of(applied)
    cards = board.by_key()
    part_two = cards.get("faker-kit:chief:2", {})
    run.step("kanban: the rerun creates only the missing part cards, with no duplicates, and the marker appears only after the last card",
             applied[0] == 0 and done["cards_existing"] == 2 and len(done["cards_created"]) == 3 and sorted(cards) == [f"faker-kit:chief:{n}" for n in range(1, 6)]
             and len(board.state()["tasks"]) == 5
             and marker.exists() and all(c["marker_present"] is False for c in board.state()["creates"]) and oct(marker.stat().st_mode & 0o777) == "0o600", json.dumps(done["cards_created"]))
    one = cards["faker-kit:chief:1"]
    run.step("kanban: a migrated part card is created by and assigned to the chief, with its outcome and fleet-ci completion contract, a History line, and no decisions copied",
             one["created_by"] == CHIEF and one["assignee"] == CHIEF and one["title"] == "Add search to the catalog" and json.loads(one["outcome_contract"]) == {"kind": "merged_pr", "repo": REPO}
             and one["completion_contract"] == f"fleet-ci:{REPO}" and f"History: loop/{CHIEF}.json#1" in lines(one) and "State: handed-out" in lines(one)
             and not any(x.startswith("Decisions") for x in lines(one))
             and json.loads(part_two["outcome_contract"]) == {"kind": "built_artifact"} and 'Stage: built=run-17' in lines(part_two) and "PRs: 11" in lines(part_two)
             and "Waits-on: shop#9" in lines(cards["faker-kit:chief:3"]) and "Reported: ready-to-merge" in lines(cards["faker-kit:chief:5"]), json.dumps(lines(one)))
    before = (board.mutations(), marker.read_bytes())
    again = chief("loop", "migrate", "--apply")
    run.step("kanban: a second apply creates nothing, makes no link, and leaves the marker as it was",
             again[0] == 0 and json_of(again)["cards_created"] == [] and board.mutations() == before[0] and marker.read_bytes() == before[1], again[2].strip())

    board.set(fail_at=board.mutations() + 3)
    broken_units = project("loop", "migrate", "--apply")
    state = board.state()
    run.step("kanban: an interrupted project apply made the unit cards but not the link, and the marker lists no units yet",
             broken_units[0] == 1 and len(state["tasks"]) == 7 and state["links"] == [] and json.loads(marker.read_text())["units"] == {}, broken_units[2].strip())
    units_done = json_of(project("loop", "migrate", "--apply"))
    state = board.state()
    cards = board.by_key()
    csv, login = cards["faker-kit:shop:csv-export"], cards["faker-kit:shop:login-fix"]
    run.step("kanban: the project rerun creates no second unit card and makes the one link, with the unit as the prerequisite (link <unit> <part>)",
             units_done["cards_created"] == [] and units_done["links_made"] == 1 and state["links"] == [[csv["id"], cards["faker-kit:chief:5"]["id"]]]
             and len(state["tasks"]) == 7, json.dumps(state["links"]))
    run.step("kanban: a migrated unit card is created by and assigned to its project bot, with the part's outcome, History, PR and Session lines, and no row text beyond ids and links",
             csv["created_by"] == BOT and csv["assignee"] == BOT and csv["title"] == "csv-export" and csv["completion_contract"] == f"fleet-ci:{REPO}"
             and "Part: 5" in lines(csv) and "PR: 12" in lines(csv) and "Session: https://claude.ai/code/session_csv" in lines(csv)
             and f"History: briefs/{BOT}/orchestrate/{PROJECT}/units.tsv#csv-export" in lines(csv) and "Part:" not in " ".join(lines(login)) and "Session" not in " ".join(lines(login))
             and sorted(json.loads(marker.read_text())["units"][PROJECT]) == sorted([csv["id"], login["id"]]), json.dumps(lines(csv)))
    mutations = board.mutations()
    project("loop", "migrate", "--apply")
    run.step("kanban: a project bot's second apply makes no write call", board.mutations() == mutations, str(board.mutations()))

    run.clock += 60
    handed = chief("handoff", "--project", PROJECT, "--part", "Build the changelog archive", "--decisions", DECISIONS, "--outcome", "built")
    part = json_of(handed)
    new_card = board.by_key().get("faker-kit:chief:10", {})
    run.step("kanban: after the cutover a handoff creates the part card with the next id, over the ledger's and the cards' highest, and prints the usual keys plus the card",
             handed[0] == 0 and part.get("id") == 10 and set(part) >= HEADER_KEYS and part["card"] == new_card["id"] and new_card["assignee"] == CHIEF
             and json.loads(new_card["outcome_contract"]) == {"kind": "built_artifact"} and "Decisions: " + DECISIONS in lines(new_card)
             and "Outcome: built" in lines(new_card), json.dumps(part)[:200])
    dup = chief("handoff", "--project", PROJECT, "--part", "build the CHANGELOG archive", "--decisions", DECISIONS)
    run.step("kanban: a part already open for the project is refused after the cutover too", dup[0] == 1 and "follow up with" in dup[2] and len(board.state()["tasks"]) == 8, dup[2].strip())

    shown_loop = json_of(chief("loop"))
    ids = [p["id"] for p in shown_loop["open"]]
    run.step("kanban: loop reads the cards with the ledger's output shape, the open parts, and what to tell the owner",
             ids == [1, 2, 3, 4, 5, 10] and all(set(p) >= HEADER_KEYS for p in shown_loop["open"]) and shown_loop["to_report"] == [] and shown_loop["open"][1]["stages"] == {"built": "run-17"}
             and shown_loop["open"][2]["waits_on"] == "shop#9", json.dumps(ids))
    run.clock += 60
    setting = json_of(chief("loop", "set", "10", "--state", "in-progress", "--pr", "31", "--pr", "30"))
    card_ten = board.by_key()["faker-kit:chief:10"]
    run.step("kanban: loop set rewrites the card's header: state, pull requests, and the updated time",
             setting.get("state") == "in-progress" and setting["prs"] == [30, 31] and "State: in-progress" in lines(card_ten) and "PRs: 30,31" in lines(card_ten)
             and f"Updated: {run.clock}" in lines(card_ten), json.dumps(lines(card_ten)))
    refuse_merge = chief("loop", "set", "10", "--state", "merged")
    bad_ref = chief("loop", "set", "10", "--evidence", "built=release.tgz")
    run.step("kanban: a built part cannot be merged without built evidence, and card evidence must be a JSON object for Kanban's schema; the card stays as it was",
             refuse_merge[0] == 1 and "built evidence" in refuse_merge[2] and bad_ref[0] == 1 and "JSON object" in bad_ref[2]
             and "State: in-progress" in lines(board.by_key()["faker-kit:chief:10"])
             and board.state()["evidence"] == [], refuse_merge[2].strip() + bad_ref[2].strip())
    board.set(evidence_refuse=True)
    payload = json.dumps({"name": "changelog.tgz", "sha256": "a" * 64})
    pal_refuses = chief("loop", "set", "10", "--state", "merged", "--evidence", f"built={payload}")
    run.step("kanban: when Kanban refuses the evidence the part is not changed and the refusal is surfaced",
             pal_refuses[0] == 1 and "did not accept the evidence" in pal_refuses[2] and "State: in-progress" in lines(board.by_key()["faker-kit:chief:10"]), pal_refuses[2].strip())
    board.set(evidence_refuse=False)
    merged = chief("loop", "set", "10", "--state", "merged", "--evidence", f"built={payload}")
    state = board.state()
    run.step("kanban: built evidence is recorded through a claim and evidence add, and the part is then merged in the same call",
             merged[0] == 0 and json_of(merged)["reached"] == "built" and [e["kind"] for e in state["evidence"]] == ["artifact_built"]
             and state["evidence"][0]["payload"] == {"name": "changelog.tgz", "sha256": "a" * 64}
             and "State: merged" in lines(board.by_key()["faker-kit:chief:10"]), merged[2].strip())
    run.clock += 60
    chief("loop", "set", "4", "--state", "abandoned")
    run.step("kanban: abandoning a part archives its card", next(t for t in board.state()["tasks"] if t["title"] == "Rework the checkout page")["status"] == "archived", "")
    run.clock += 60
    first = json_of(chief("loop", "digest"))
    second = json_of(chief("loop", "digest"))
    run.step("kanban: the digest reads the cards, names the closed parts once, and starts the next window",
             sorted(p["id"] for p in first["closed"]) == [4, 10] and second["closed"] == [] and sorted(p["id"] for p in first["in_flight"]) == [1, 2, 3, 5]
             and [p["id"] for p in first["waiting_on_owner"]] == [5],
             json.dumps([p["id"] for p in first["closed"]]))

    try:
        loop_store(run.team, CHIEF).add(PROJECT, BOT, "A late part", DECISIONS, run.clock)
        write_refused = ""
    except loop_error as err:
        write_refused = str(err)
    run.step("kanban: the old loop JSON is byte for byte what it was before the cutover, and a write to it is refused",
             sha(ledger_file.read_text()) == ledger_hash and "read-only history" in write_refused, write_refused)

    unit = json_of(project("loop", "unit", "parser-fix", "--part", "3", "--pr", "40"))
    cards = board.by_key()
    parser_card = cards["faker-kit:shop:parser-fix"]
    run.step("kanban: loop unit creates the unit card for the project bot, with the part's outcome, and links it as the part's prerequisite",
             unit.get("created") is True and unit["linked"] is True and parser_card["assignee"] == BOT and "Part: 3" in lines(parser_card) and "PRs: 40" in lines(parser_card)
             and [parser_card["id"], cards["faker-kit:chief:3"]["id"]] in board.state()["links"], json.dumps(unit))
    mutations = board.mutations()
    repeat = json_of(project("loop", "unit", "parser-fix", "--part", "3"))
    unknown_part = project("loop", "unit", "other-fix", "--part", "77")
    run.step("kanban: loop unit twice makes no second card and no second link, and a part that is not there is refused",
             repeat.get("created") is False and repeat["linked"] is False and board.mutations() == mutations and unknown_part[0] == 1 and "no part 77" in unknown_part[2], unknown_part[2].strip())
    listed = json_of(project("loop"))
    run.step("kanban: a project bot's loop lists its own unit cards", sorted(u["unit"] for u in listed["units"]) == ["csv-export", "login-fix", "parser-fix"], json.dumps(listed)[:200])
    wrong_role = [project("loop", "set", "1", "--state", "blocked"), project("loop", "digest"), chief("loop", "unit", "x-1")]
    run.step("kanban: a project bot may only show, migrate and unit, and the chief has no unit action",
             all(r[0] == 1 and "not one of" in r[2] for r in wrong_role), " | ".join(r[2].strip() for r in wrong_role))

    board.set(dispatch_mode="workers")
    mutations = board.mutations()
    blocked = [chief("handoff", "--project", PROJECT, "--part", "Another part", "--decisions", DECISIONS), chief("loop", "set", "1", "--reported"),
               project("loop", "unit", "late-unit"), chief("loop", "migrate", "--apply")]
    reading = chief("loop")
    run.step("kanban: every write is refused while the board spawns workers, and reading still works",
             all(r[0] == 1 and "not an external board" in r[2] for r in blocked) and board.mutations() == mutations and reading[0] == 0, " | ".join(r[2].strip()[:60] for r in blocked))
    board.set(dispatch_mode="external")

    verbs = set(board.verbs())
    flat = {v[0] for v in verbs}
    tokens = {token for _, rest in board.calls() for token in rest}
    forbidden = {"dispatch", "daemon", "swarm", "specify", "decompose", "promote", "unblock", "--force", "set-dispatch-mode", "switch", "complete", "rm"}
    run.step("kanban: the Kanban calls are only list, show, create, link, edit, claim, evidence and archive; no worker, dispatch, forced completion, or board change appears",
             flat <= ALLOWED_VERBS and not tokens & forbidden and ("boards", "create") not in verbs, ",".join(sorted(flat)))
    claims = [profile for profile, rest in board.calls() if "claim" in rest[:3]]
    run.step("kanban: a claim is made only by the card's own assignee, to record evidence", claims == [CHIEF], json.dumps(claims))
