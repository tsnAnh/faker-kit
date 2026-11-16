from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
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
        if st.get("boards_fail"):
            die("database is locked")
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
        print(json.dumps({"task": found, "parents": parents_of(found["id"]), "children": [c for p, c in st["links"] if p == found["id"]], "comments": [],
                          "events": [e for e in st.get("events", []) if e["task_id"] == found["id"]],
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
        found, positional = opts(rest)
        if "--force" in found or "--metadata" not in found:
            die("complete takes no --force and needs --metadata", 2)
        card = task(positional[0])
        run_id = int(found["--run-id"])
        live = next((r for r in st["runs"] if r["id"] == run_id and card and r["task_id"] == card["id"] and r["ended_at"] is None), None)
        if card is None or live is None or card["status"] != "running":
            die("cannot complete: no live run")
        kinds = sorted(e["kind"] for e in st["evidence"] if e["task_id"] == card["id"] and e["run_id"] == run_id)
        if json.loads(card["outcome_contract"])["kind"] == "merged_pr" and not {"pr_published", "pr_merged"} <= set(kinds):
            die("cannot complete: the outcome contract needs verified pull request evidence")
        refuse = st.get("complete_refuse")
        published = json.loads(found["--metadata"])["published_pr"]
        if not refuse or not (refuse.startswith("text-") or refuse == "bare"):
            st.setdefault("events", []).append({"task_id": card["id"], "kind": "pr_acceptance", "run_id": run_id, "created_at": 1,
                                                "payload": {"ok": not refuse, "classification": refuse or "success", "detail": f"CI acceptance {refuse or 'success'} on the exact head"}})
        st.setdefault("completions", []).append({"task": card["id"], "run_id": run_id, "published": published, "kinds": kinds, "profile": profile})
        if refuse:
            die(f"cannot complete {card['id']}: PR acceptance {refuse[5:]}: no proof yet" if refuse.startswith("text-") else f"cannot complete {card['id']} (unknown id or terminal state)")
        card["status"], live["ended_at"] = "done", 2
    save()
'''

CHIEF = "ba-truong"
BOT = "meo-beo"
PROJECT = "shop"
REPO = "acme/shop"
DECISIONS = "none: bug fix, expected behaviour obvious from the report"
ALLOWED_VERBS = {"boards", "list", "show", "create", "link", "edit", "claim", "evidence", "archive"}
FORBIDDEN_TOKENS = {"dispatch", "daemon", "swarm", "specify", "decompose", "promote", "unblock", "--force", "set-dispatch-mode", "switch", "rm"}
HEADER_KEYS = {"id", "project", "bot", "part", "decisions", "outcome", "stages", "reached", "state", "prs", "waits_on", "added", "updated", "reported"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Board:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.path = run.hermes / "kanban-state.json"
        self.path.write_text(json.dumps({"dispatch_mode": "external", "tasks": [], "links": [], "evidence": [], "runs": [], "mutations": 0, "fail_at": None, "next": 1, "creates": [],
                                                "events": [], "completions": []}))

    def state(self) -> dict:
        return json.loads(self.path.read_text())

    def set(self, **changes: object) -> None:
        data = self.state()
        data.update(changes)
        self.path.write_text(json.dumps(data))

    def edit_card(self, key: str, **changes: object) -> None:
        data = self.state()
        for task in data["tasks"]:
            if f"Key: {key}" in task["body"].splitlines():
                task.update(changes)
        self.path.write_text(json.dumps(data))

    def add_evidence(self, key: str, kind: str, payload: dict) -> None:
        data = self.state()
        card = next(t for t in data["tasks"] if f"Key: {key}" in t["body"].splitlines())
        data["evidence"].append({"id": len(data["evidence"]) + 1, "task_id": card["id"], "run_id": 1, "kind": kind, "payload": payload})
        self.path.write_text(json.dumps(data))

    def add_card(self, key: str, title: str, body: str, assignee: str = BOT, status: str = "ready") -> str:
        data = self.state()
        ident = f"t_{data['next']}"
        data["next"] += 1
        data["tasks"].append({"id": ident, "title": title, "body": body, "assignee": assignee, "status": status, "created_by": assignee, "completion_contract": f"fleet-ci:{REPO}",
                              "outcome_contract": json.dumps({"kind": "merged_pr", "repo": REPO}), "result": None, "idempotency": key})
        self.path.write_text(json.dumps(data))
        return ident

    def legacy(self, key: str) -> None:
        data = self.state()
        for task in data["tasks"]:
            if f"Key: {key}" in task["body"].splitlines():
                task["body"] = "\n".join(lines(task)) + "\n\n"
        self.path.write_text(json.dumps(data))

    def body(self, key: str) -> str:
        return str(self.by_key()[key]["body"])

    def mutating(self) -> list[str]:
        return [verb[0] for verb in self.verbs() if verb[0] in ("create", "link", "edit", "claim", "evidence", "complete", "archive")]

    def mutations(self) -> int:
        return int(self.state()["mutations"])

    def by_key(self) -> dict[str, dict]:
        return {line.partition(": ")[2]: t for t in self.state()["tasks"] for line in t["body"].splitlines() if line.startswith("Key: ")}

    def calls(self) -> list[tuple[str, list[str]]]:
        found = []
        log = self.run.hermes / "hermes-calls.log"
        for line in log.read_text().splitlines() if log.exists() else []:
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


FOOTER = "---- faker-kit machine fields (read by the kit, do not edit) ----"


def lines(task: dict) -> list[str]:
    rows = task["body"].splitlines()
    return rows[rows.index(FOOTER) + 1:] if FOOTER in rows else task["body"].partition("\n\n")[0].splitlines()


def readable(task: dict) -> str:
    rows = task["body"].splitlines()
    return "\n".join(rows[:rows.index(FOOTER)]).rstrip("\n") if FOOTER in rows else ""


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
    forbidden = {*FORBIDDEN_TOKENS, "complete"}
    run.step("kanban: the Kanban calls are only list, show, create, link, edit, claim, evidence and archive; no worker, dispatch, forced completion, or board change appears",
             flat <= ALLOWED_VERBS and not tokens & forbidden and ("boards", "create") not in verbs, ",".join(sorted(flat)))
    claims = [profile for profile, rest in board.calls() if "claim" in rest[:3]]
    run.step("kanban: a claim is made only by the card's own assignee, to record evidence", claims == [CHIEF], json.dumps(claims))


def head_of(n: int) -> str:
    return f"{n + 1000:040x}"


def commit_of(n: int) -> str:
    return f"{n:040x}"


def kanban_merge_scenario(run: Run, open_gates: Callable[[Run, int, str], None]) -> None:
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1))
    board = Board(run)
    ledger(run)
    ledger_file = run.team / "loop" / f"{CHIEF}.json"
    tsv = run.team / "briefs" / BOT / "orchestrate" / PROJECT / "units.tsv"
    tsv.write_text(tsv.read_text() + "pay-fix\trunning\tb\t47\t" + "c" * 40 + "\n")
    marker = run.team / "kanban-cutover.json"

    def chief(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=CHIEF)

    def project(*argv: str) -> tuple[int, str, str]:
        return run.kit(*argv, bot=BOT)

    def add_pr(n: int, unit: str | None = None, **fields: object) -> None:
        body = f"Unit: {unit}\n\nChange for {unit}" if unit else "A change with no unit line"
        run.update_gh(lambda state: state["pulls"].append({**run.pull(n, head_of(n)), "body": body, **fields}))

    def settled(n: int, merged: bool, **fields: object) -> None:
        extra = {"state": "closed", "merged_at": "2026-10-10T00:00:00Z", "merge_commit_sha": commit_of(n)} if merged else {"state": "closed", "merged_at": None}
        add_pr(n, None, **extra, **fields)

    def merged_prs() -> list[int]:
        return [m["pr"] for m in run.merged()]

    def part(ident: int) -> dict:
        return next(p for p in json.loads(ledger_file.read_text())["parts"] if p["id"] == ident)

    def ready(n: int) -> None:
        open_gates(run, n, head_of(n))

    add_pr(10, "pre-unit")
    ready(10)
    pre = project("merge", "--pr", "10")
    run.step("kanban merge: before the cutover marker a merge makes no Kanban call at all and prints no card",
             pre[0] == 0 and merged_prs() == [10] and board.calls() == [] and "card" not in json_of(pre), pre[2].strip())

    for ident, prs in ((1, "41"), (3, "42"), (4, "43")):
        chief("loop", "set", str(ident), "--pr", prs)
    for text, pr in (("Build the nightly archive", "44"), ("Fix the cart total", "45"), ("Add dark mode", "46")):
        made = json_of(chief("handoff", "--project", PROJECT, "--part", text, "--decisions", DECISIONS, *(["--outcome", "built"] if "archive" in text else [])))
        chief("loop", "set", str(made["id"]), "--state", "in-progress", "--pr", pr)
    settled(41, True)
    settled(42, False)
    add_pr(43)
    add_pr(44, None, state="closed", merged_at="2026-10-10T00:00:00Z", merge_commit_sha=commit_of(44))
    add_pr(45, None, state="unknown")
    add_pr(46)
    settled(47, True)
    ledger_hash, tsv_hash = sha(ledger_file.read_text()), sha(tsv.read_text())

    dry = json_of(chief("loop", "migrate", "--dry-run"))
    pdry = json_of(project("loop", "migrate", "--dry-run"))
    section, psection = dry.get("reconcile", {}), pdry.get("reconcile", {})
    baseline = len(board.calls())
    run.step("kanban reconcile: the dry run reports what a reconcile would do from GitHub (merged, closed, unchanged, unreadable) and writes nothing",
             section.get("read") == 8 and section["would_set_merged"] == 1 and section["would_set_closed"] == 1 and section["unchanged"] == 2 and section["unreadable"] == 3
             and section["entries"]["merged"] == ["1"] and section["entries"]["closed"] == ["3"] and sorted(section["entries"]["unchanged"]) == ["12", "4"]
             and sorted(u["id"] for u in section["entries"]["unreadable"]) == ["11", "2", "5"] and psection["would_set_merged"] == 1 and psection["entries"]["merged"] == ["pay-fix"]
             and [u["id"] for u in psection["entries"]["unreadable"]] == ["csv-export"] and sha(ledger_file.read_text()) == ledger_hash and sha(tsv.read_text()) == tsv_hash
             and board.mutations() == 0 and not marker.exists(), json.dumps(section)[:300])

    first = json_of(chief("loop", "reconcile"))
    rows = {p["id"]: p for p in json_of(chief("loop", "show", "--all"))["open"]}
    run.step("kanban reconcile: a part whose pull request merged on GitHub with no message is set merged, a closed unmerged one abandoned, an open one left, and a built part awaits its evidence",
             first["set_merged"] == 1 and first["entries"]["merged"] == ["1"] and first["entries"]["closed"] == ["3"] and first["entries"]["unchanged"] == ["4", "12"]
             and first["entries"]["awaiting_stage_evidence"] == [{"id": "10", "outcome": "built"}] and rows[1]["state"] == "merged" and rows[3]["state"] == "abandoned"
             and rows[4]["state"] == "blocked" and rows[10]["state"] == "in-progress" and rows[10]["reached"] == "none", json.dumps(first["entries"])[:300])
    run.step("kanban reconcile: a GitHub read that fails or answers in a shape faker-kit does not trust changes nothing and is reported unreadable with its reason",
             sorted(u["id"] for u in first["entries"]["unreadable"]) == ["11", "2", "5"] and all(u["reason"] for u in first["entries"]["unreadable"])
             and rows[11]["state"] == "in-progress" and rows[5]["state"] == "ready-to-merge" and rows[2]["state"] == "in-progress"
             and any("usable state" in u["reason"] for u in first["entries"]["unreadable"] if u["id"] == "11"), json.dumps(first["entries"]["unreadable"])[:300])
    settled_hash = sha(ledger_file.read_text())
    again = json_of(chief("loop", "reconcile"))
    run.step("kanban reconcile: a second run changes nothing and says unchanged",
             again["set_merged"] == 0 and again["set_closed"] == 0 and again["entries"]["unchanged"] == ["4", "12"] and sha(ledger_file.read_text()) == settled_hash, json.dumps(again)[:200])

    run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == 46).update(state="closed", merged_at="2026-10-11T00:00:00Z", merge_commit_sha=commit_of(46)))
    digest = json_of(chief("loop", "digest"))
    run.step("kanban reconcile: the digest reconciles first, so a part merged on GitHub with no message is in the digest as merged, and an unreadable read is named and leaves its part",
             digest["reconcile"]["set_merged"] == 1 and 12 in [p["id"] for p in digest["closed"]] and "11" in digest["reconcile"]["unreadable_ids"] and part(11)["state"] == "in-progress"
             and part(12)["state"] == "merged", json.dumps(digest["reconcile"]))

    reported = json_of(project("loop", "reconcile"))
    run.step("kanban reconcile: a project bot before the cutover only reports what it would set; units.tsv, the ledger, and Kanban are untouched",
             reported["mode"] == "report" and reported["set_merged"] == 0 and reported["would_set"]["merged"] == ["pay-fix"] and sha(tsv.read_text()) == tsv_hash
             and [u["id"] for u in reported["entries"]["unreadable"]] == ["csv-export"] and len(board.calls()) == baseline and board.mutations() == 0,
             json.dumps(reported)[:300])

    assert chief("loop", "migrate", "--apply")[0] == 0
    assert project("loop", "migrate", "--apply")[0] == 0
    cards = board.by_key()
    run.step("kanban merge: the cutover is applied for the merge gate matrix (part cards for open parts, unit cards for the project's open units)",
             marker.exists() and "faker-kit:shop:csv-export" in cards and "faker-kit:shop:pay-fix" in cards and "faker-kit:chief:10" in cards
             and "faker-kit:chief:12" not in cards, ",".join(sorted(cards)))

    def card_key(unit: str) -> str:
        return f"faker-kit:{PROJECT}:{unit}"

    def case(label: str, n: int, unit: str | None, needle: str, create: bool = True, prepare: Callable[[], None] | None = None, undo: Callable[[], None] | None = None) -> None:
        add_pr(n, unit)
        if unit and create:
            assert project("loop", "unit", unit, "--pr", str(n))[0] == 0
        if prepare:
            prepare()
        ready(n)
        refused = project("merge", "--pr", str(n))
        not_merged = n not in merged_prs()
        base = len(board.calls())
        approved = project("merge", "--pr", str(n), "--owner-approved")
        run.step(f"kanban merge gate {label}: a labeled pull request with CI green on its exact head is refused, and --owner-approved merges it with no Kanban call",
                 refused[0] == 1 and needle in refused[2] and not_merged and approved[0] == 0 and n in merged_prs() and len(board.calls()) == base and "card" not in json_of(approved),
                 refused[2].strip())
        if undo:
            undo()

    case("(a) no unit card", 21, "ghost-a", "has no unit card for ghost-a", create=False)
    case("(a) no Unit line", 22, None, "no `Unit: <id>` first line")
    case("(b) card of another bot", 23, "other-b", "belongs to rival-bot, not meo-beo", prepare=lambda: board.edit_card(card_key("other-b"), assignee="rival-bot"))
    case("(c) card done", 24, "done-c", "already done", prepare=lambda: board.edit_card(card_key("done-c"), status="done"))
    case("(c) card archived", 25, "arch-c", "already archived", prepare=lambda: board.edit_card(card_key("arch-c"), status="archived"))
    case("(d) board unreadable", 26, "open-d", "could not read Kanban", prepare=lambda: board.set(boards_fail=True), undo=lambda: board.set(boards_fail=False))
    case("(e) board not external", 27, "open-e", "not an external board", prepare=lambda: board.set(dispatch_mode="workers"), undo=lambda: board.set(dispatch_mode="external"))
    case("(f) card bound to another pull request", 28, "bound-f", "bound to another pull request",
         prepare=lambda: board.add_evidence(card_key("bound-f"), "pr_published", {"pr_url": f"https://github.com/{REPO}/pull/99", "head_sha": "d" * 40}))

    add_pr(30, "ship-ok")
    assert project("loop", "unit", "ship-ok", "--pr", "30")[0] == 0
    ready(30)
    shipped = project("merge", "--pr", "30")
    ship = json_of(shipped)
    card = board.by_key()[card_key("ship-ok")]
    state = board.state()
    rows_for = [e for e in state["evidence"] if e["task_id"] == card["id"]]
    run.step("kanban merge: an open card owned by the caller lets the merge land and the card complete through Pal's rule: claim, pr_published, pr_merged with the merge commit, complete",
             shipped[0] == 0 and ship["card"] == {"id": card["id"], "completion": "done"} and card["status"] == "done" and [e["kind"] for e in rows_for] == ["pr_published", "pr_merged"]
             and rows_for[1]["payload"] == {"pr_url": f"https://github.com/{REPO}/pull/30", "head_sha": head_of(30), "merge_commit": commit_of(30)}
             and [c["published"] for c in state["completions"] if c["task"] == card["id"]] == [f"https://github.com/{REPO}/pull/30"]
             and [c["profile"] for c in state["completions"]] == [BOT], shipped[1].strip() or shipped[2].strip())

    add_pr(29, "legacy-u")
    assert project("loop", "unit", "legacy-u", "--pr", "29")[0] == 0
    board.legacy(card_key("legacy-u"))
    ready(29)
    legacy_ship = json_of(project("merge", "--pr", "29"))
    legacy_card = board.by_key()[card_key("legacy-u")]
    run.step("kanban merge: an old-layout card (fields on top, no footer) passes the card gate and completes through Pal's rule like a new one",
             legacy_ship["card"] == {"id": legacy_card["id"], "completion": "done"} and legacy_card["status"] == "done" and FOOTER not in legacy_card["body"], json.dumps(legacy_ship.get("card")))

    pal_cases = [("pending", "pending"), ("failure", "failure"), ("stale", "stale"), ("provenance", "provenance"), ("mismatch", "mismatch"), ("text-infra", "infra"), ("bare", "unknown")]
    for index, (mode, wanted) in enumerate(pal_cases):
        n, unit = 31 + index, f"pal-{mode}"
        add_pr(n, unit)
        assert project("loop", "unit", unit, "--pr", str(n))[0] == 0
        ready(n)
        board.set(complete_refuse=mode)
        result = project("merge", "--pr", str(n))
        out = json_of(result)
        card = board.by_key()[card_key(unit)]
        state = board.state()
        run.step(f"kanban merge: Pal refusing completion ({wanted}) leaves the pull request merged and the card open, and the merge reports the card once with its classification",
                 result[0] == 0 and out["merged"] is True and n in merged_prs() and out["card"]["completion"] == "refused" and out["card"]["classification"] == wanted
                 and out["card"]["id"] == card["id"] and out["card"]["detail"] and card["status"] == "running"
                 and len([c for c in state["completions"] if c["task"] == card["id"]]) == 1 and len([r for r in state["runs"] if r["task_id"] == card["id"]]) == 1,
                 json.dumps(out.get("card")))
    board.set(complete_refuse=None)

    add_pr(60, "wait-u")
    add_pr(61, "wait-v")
    assert project("loop", "unit", "wait-u", "--pr", "60")[0] == 0
    assert project("loop", "unit", "wait-v", "--pr", "61")[0] == 0
    def hold(n: int) -> None:
        run.update_gh(lambda state: state["check_runs"].__setitem__(head_of(n), [{"name": "check", "status": "in_progress", "conclusion": ""}]))

    for n, unit, change, needle in ((60, "wait-u", {"assignee": "rival-bot"}, "belongs to rival-bot"), (61, "wait-v", {"status": "done"}, "already done")):
        ready(n)
        hold(n)
        run.sleeps.clear()
        run.on_sleep = [lambda unit=unit, change=change: board.edit_card(card_key(unit), **change)]
        waiting = project("merge", "--pr", str(n), "--wait", "300")
        run.on_sleep = []
        run.step(f"kanban merge: a card changed while --wait polls ({needle}) refuses on the next poll and merges nothing",
                 waiting[0] == 1 and needle in waiting[2] and n not in merged_prs() and run.sleeps == [30.0], f"{waiting[2].strip()} {run.sleeps}")

    add_pr(62, "ci-u")
    assert project("loop", "unit", "ci-u", "--pr", "62")[0] == 0
    ready(62)
    run.update_gh(lambda state: state["check_runs"].__setitem__(head_of(62), [{"name": "check", "status": "completed", "conclusion": "failure"}]))
    red = project("merge", "--pr", "62")
    add_pr(63, "cord-u")
    assert project("loop", "unit", "cord-u", "--pr", "63")[0] == 0
    ready(63)
    raised = project("andon", "raise", "card", "gate", "probe")
    cord = project("merge", "--pr", "63")
    project("andon", "clear")
    run.step("kanban merge: after the cutover a failed CI check and a pulled andon cord refuse an owned card's pull request exactly as before",
             red[0] == 1 and "checks have not all passed" in red[2] and cord[0] == 1 and "andon" in cord[2].lower() and raised[0] == 0 and 62 not in merged_prs() and 63 not in merged_prs(),
             red[2].strip() + cord[2].strip())

    add_pr(51)
    run.update_gh(lambda state: next(p for p in state["pulls"] if p["number"] == 51).update(state="closed", merged_at=None, body="Unit: dead-u"))
    assert project("loop", "unit", "dead-u", "--pr", "51")[0] == 0

    board.set(complete_refuse="pending")
    before = board.state()
    stuck = json_of(project("loop", "reconcile"))
    state = board.state()
    pal_units = [f"pal-{mode}" for mode, _ in pal_cases]
    refused_ids = {r["id"]: r for r in stuck["entries"]["refused"]}
    runs_per = {u: len([r for r in state["runs"] if r["task_id"] == board.by_key()[card_key(u)]["id"]]) for u in pal_units}
    evidence_per = {u: len([e for e in state["evidence"] if e["task_id"] == board.by_key()[card_key(u)]["id"]]) for u in pal_units}
    dead = board.by_key()[card_key("dead-u")]
    run.step("kanban reconcile: after the cutover a merged pull request completes its unit card only on Pal's accepted receipt; a refusal leaves it open, reuses its run, adds no evidence twice",
             all(refused_ids[u]["classification"] == "pending" for u in pal_units) and all(v == 1 for v in runs_per.values()) and all(v == 2 for v in evidence_per.values())
             and all(board.by_key()[card_key(u)]["status"] == "running" for u in pal_units)
             and len(state["completions"]) > len(before["completions"]), json.dumps(stuck["entries"]["refused"])[:300])
    run.step("kanban reconcile: a closed unmerged pull request abandons its unit card and archives it, and a GitHub read error on another unit changes nothing",
             "dead-u" in stuck["entries"]["closed"] and dead["status"] == "archived" and "State: abandoned" in lines(dead) and "csv-export" in [u["id"] for u in stuck["entries"]["unreadable"]]
             and "State: in-progress" in lines(board.by_key()[card_key("csv-export")]), json.dumps(stuck["entries"]["closed"]))
    board.set(complete_refuse=None)
    done = json_of(project("loop", "reconcile"))
    state = board.state()
    run.step("kanban reconcile: once Pal accepts, the open units complete without a second claim or duplicate evidence, and their header says merged",
             all(u in done["entries"]["merged"] for u in pal_units)
             and all(board.by_key()[card_key(u)]["status"] == "done" and "State: merged" in lines(board.by_key()[card_key(u)]) for u in pal_units)
             and all(len([e for e in state["evidence"] if e["task_id"] == board.by_key()[card_key(u)]["id"]]) == 2 for u in pal_units)
             and all(len([r for r in state["runs"] if r["task_id"] == board.by_key()[card_key(u)]["id"]]) == 1 for u in pal_units), json.dumps(done["entries"]["merged"]))
    mutations = board.mutations()
    quiet = json_of(project("loop", "reconcile"))
    run.step("kanban reconcile: a second run after completion changes nothing", quiet["set_merged"] == 0 and quiet["set_closed"] == 0 and board.mutations() == mutations, json.dumps(quiet)[:200])

    for text, pr, merged in (("Add the wishlist", 48, True), ("Add reviews", 49, True), ("Add gift cards", 50, False)):
        made = json_of(chief("handoff", "--project", PROJECT, "--part", text, "--decisions", DECISIONS))
        assert chief("loop", "set", str(made["id"]), "--state", "in-progress", "--pr", str(pr))[0] == 0
        settled(pr, merged)
    assert project("loop", "unit", "u14", "--part", "14")[0] == 0
    board.set(complete_refuse="pending")
    stuck = json_of(chief("loop", "reconcile"))
    cards = board.by_key()
    unit_card = cards[card_key("u14")]
    run.step("kanban reconcile: the chief completes a part only through Pal, waits for the part's unit cards, leaves a built part awaiting its evidence, and abandons a closed part",
             {r["id"]: r["classification"] for r in stuck["entries"]["refused"]} == {"13": "pending"}
             and stuck["entries"]["waiting_on_units"] == [{"id": "14", "card": cards["faker-kit:chief:14"]["id"], "units": [unit_card["id"]]}]
             and stuck["entries"]["awaiting_stage_evidence"] == [{"id": "10", "outcome": "built"}] and stuck["entries"]["closed"] == ["15"] and cards["faker-kit:chief:15"]["status"] == "archived"
             and cards["faker-kit:chief:13"]["status"] == "running" and cards["faker-kit:chief:14"]["status"] != "done" and cards["faker-kit:chief:10"]["status"] != "done",
             json.dumps(stuck["entries"])[:300])
    board.set(complete_refuse=None)
    finished = json_of(chief("loop", "reconcile"))
    cards = board.by_key()
    part_rows = {p["id"]: p for p in json_of(chief("loop", "show", "--all"))["open"]}
    kinds = [e["kind"] for e in board.state()["evidence"] if e["task_id"] == cards["faker-kit:chief:13"]["id"]]
    mutations = board.mutations()
    final = json_of(chief("loop", "reconcile"))
    run.step("kanban reconcile: with an accepted receipt the chief's part card completes with its pull request evidence, shows merged, and a further run changes nothing",
             finished["entries"]["merged"] == ["13"] and cards["faker-kit:chief:13"]["status"] == "done" and kinds == ["pr_published", "pr_merged"] and part_rows[13]["state"] == "merged"
             and final["set_merged"] == 0 and board.mutations() == mutations, json.dumps(finished["entries"]["merged"]))

    calls = board.calls()
    flat = {v[0] for v in set(board.verbs())}
    tokens = {token for _, rest in calls for token in rest}
    completes = [rest for _, rest in calls if "complete" in rest[:3]]
    run.step("kanban merge and reconcile: Kanban calls are only the allowed verbs and complete, each complete names its run, and no forced completion, dispatch, worker, or board change appears",
             flat <= ALLOWED_VERBS | {"complete"} and not tokens & FORBIDDEN_TOKENS and completes and all("--run-id" in c and "--metadata" in c for c in completes), ",".join(sorted(flat)))
