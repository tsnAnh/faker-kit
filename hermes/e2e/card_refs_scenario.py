from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from kanban_scenario import BOT, CHIEF, FOOTER, Board, json_of, lines, readable

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

FK_BOT = "fk-bot"
FREN_BOT = "fren-bot"
NOPREF_BOT = "np-bot"
PAL_BOT = "pal-bot"
SHOP_BOT = BOT
TABLE = {"hermes-bot-mobile": "PAL", "fren": "FREN", "smilewallpaper": "SMILE", "charmeji": "CHARM", "memolly": "MEM", "paint_by_number": "PBN", "kith": "KITH",
         "data-warehouse": "DWH", "faker-kit": "FK"}
LABELS = ("**Goal:**", "**Done means:**", "**Current status:**", "**Owner decisions:**")
PAL_FIELDS = {"Key", "Kind", "Project", "Bot", "Outcome", "State", "Waits-on", "PRs", "Reported", "Part", "Added", "Updated", "Decisions", "Run", "Work", "Stage", "History", "PR", "Session"}
DECISIONS = "none: the work is obvious from the report"
MACHINE_WORDS = ("Unit of", "run by", "serves no chief part", "Status:", "Kanban run")


def labels_of(text: str) -> list[str]:
    return [p.partition(":**")[0] + ":**" for p in text.split("\n\n")[1:]]


def setup(run: Run) -> tuple[Board, Callable[..., tuple[int, str, str]]]:
    bots = run.team / "bots.yaml"
    extra = "".join(f"  {bot}:\n    role: project\n    project: {project}\n    repo: acme/{project}\n"
                    for bot, project in ((FK_BOT, "faker-kit"), (FREN_BOT, "fren"), (NOPREF_BOT, "nopref"), (PAL_BOT, "hermes-bot-mobile")))
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1) + extra)
    (run.team / "kanban-cutover.json").write_text(json.dumps({"at": 1, "board": "default", "parts": [], "units": {}}))
    board = Board(run)
    return board, lambda *argv, bot: run.kit(*argv, bot=bot)


def old_card(board: Board, key: str, project: str, bot: str, kind: str, added: float, state: str = "in-progress", status: str = "ready", title: str | None = None, **extra: str) -> str:
    slug = key.rsplit(":", 1)[1]
    fields = {"Key": key, "Kind": kind, "Project": project, "Bot": bot, "Outcome": "merged-pr", "State": state, "Added": str(added), "Updated": str(added + 10), **extra}
    text = "\n".join([f"Fix the {slug} report from the owner. It was reported twice.", f"Unit of {project}, run by {bot}; serves no chief part.", "",
                      "Owner decisions: none recorded", "Pull requests: none yet", "Sessions: none yet", f"Status: {state}; not waiting; updated 2027-01-01 00:00 UTC"])
    body = "\n".join([text, "", FOOTER, *(f"{k}: {v}" for k, v in fields.items())])
    return board.add_card(key, title or slug, body, CHIEF if kind == "part" else bot, status)


def card_refs_scenario(run: Run) -> None:
    board, kit = setup(run)

    def made(argv: tuple[str, ...], bot: str) -> dict:
        return json_of(kit(*argv, bot=bot))

    one = made(("loop", "unit", "csv-export", "--title", "Admins can export the catalog as CSV"), FK_BOT)
    two = made(("loop", "unit", "login-fix", "--title", "Sign in works again after a password reset"), FK_BOT)
    part = made(("handoff", "--project", "faker-kit", "--part", "Let the owner mention a card by a short reference", "--decisions", DECISIONS, "--title", "Cards show a short reference"), CHIEF)
    fren = made(("loop", "unit", "avatar", "--title", "Profile photos keep their shape"), FREN_BOT)
    cards = board.by_key()
    csv = cards["faker-kit:faker-kit:csv-export"]
    run.step("card refs: two unit cards and a part card in one project get FK-1, FK-2, FK-3 and a second project starts its own sequence at FREN-1",
             [one["ref"], two["ref"], part["ref"], fren["ref"]] == ["FK-1", "FK-2", "FK-3", "FREN-1"] and csv["title"] == "FK-1 Admins can export the catalog as CSV"
             and cards["faker-kit:chief:" + str(part["id"])]["title"] == "FK-3 Cards show a short reference" and "Ref: FK-1" in lines(csv) and "Ref: FREN-1" in lines(cards["faker-kit:fren:avatar"]),
             json.dumps([one["ref"], two["ref"], part["ref"], fren["ref"]]))
    text = readable(csv)
    run.step("card refs: a new card reads as a ticket: the ref line first, then Goal, Done means, "
             "Current status and Owner decisions as bold-labelled paragraphs, no machine words, and no copy of the title",
             text.splitlines()[0] == "Ref: FK-1" and labels_of(text) == list(LABELS) and not any(w in text for w in MACHINE_WORDS)
             and csv["title"] not in text and csv["body"].count(FOOTER) == 1 and "Key: faker-kit:faker-kit:csv-export" in lines(csv), text)

    closed = made(("loop", "close", "login-fix", "--reason", "the owner dropped it"), FK_BOT)
    after_close = made(("loop", "unit", "export-pdf", "--title", "Admins can export the catalog as a PDF"), FK_BOT)
    mutations, creates = board.mutations(), len(board.state()["creates"])
    retry = made(("loop", "unit", "csv-export", "--pr", "9", "--title", "A different phrase"), FK_BOT)
    run.step("card refs: closing a card never frees its number (the next card is FK-4), and a create retry for an existing key keeps its ref and its title",
             closed["archived"] is True and after_close["ref"] == "FK-4" and retry["created"] is False and retry["ref"] == "FK-1" and retry["title"] == "FK-1 Admins can export the catalog as CSV"
             and len(board.state()["creates"]) == creates and board.mutations() > mutations, json.dumps([after_close["ref"], retry["ref"]]))

    mutations = board.mutations()
    refusals = {
        "a project with no prefix row": kit("loop", "unit", "x-one", "--title", "Anything at all", bot=NOPREF_BOT),
        "no title": kit("loop", "unit", "x-two", bot=FK_BOT),
        "an empty title": kit("loop", "unit", "x-five", "--title", "  ", bot=FK_BOT),
        "the unit id as the title": kit("loop", "unit", "x-six", "--title", "x-six", bot=FK_BOT),
        "a slug-like title": kit("loop", "unit", "x-seven", "--title", "export_catalog-csv", bot=FK_BOT),
        "a title over the limit": kit("loop", "unit", "x-three", "--title", " ".join(["Words"] * 12), bot=FK_BOT),
        "a title that looks like a key": kit("loop", "unit", "x-four", "--title", "Use ghp_" + "Q" * 24 + " here", bot=FK_BOT),
    }
    run.step("card refs: a project with no prefix row, a missing, empty, unit-id or slug-like title (with the flag and an example named), "
             "a title that does not fit with its ref, and a secret-like title are refused and create nothing ("
             + ", ".join(refusals) + ")", all(r[0] != 0 for r in refusals.values()) and board.mutations() == mutations
             and "nopref has no card prefix" in refusals["a project with no prefix row"][2] and "PREFIXES in hermes/lib/faker_kit_refs.py" in refusals["a project with no prefix row"][2]
             and "--title" in refusals["no title"][2]
             and all('--title "Admins can export the catalog as CSV"' in refusals[k][2] for k in ("an empty title", "the unit id as the title", "a slug-like title"))
             and "at most" in refusals["a title over the limit"][2] and "token or key" in refusals["a title that looks like a key"][2],
             " | ".join(f"{k}: {v[2].strip()[:70]}" for k, v in refusals.items()))

    mutations = board.mutations()
    body_before = board.by_key()["faker-kit:faker-kit:csv-export"]["body"]
    shown = made(("loop", "show", "fk-1"), FK_BOT)
    foreign = made(("loop", "show", "FK-1"), FREN_BOT)
    from_chief = made(("loop", "show", "Fk-1"), CHIEF)
    missing = kit("loop", "show", "FK-99", bot=FK_BOT)
    run.step("card refs: loop show resolves a ref in any case, prints ref, title, project, bot, unit, part, state, PRs, session and card id, "
             "names the owner and route_to for another project, and changes nothing",
             shown["ref"] == "FK-1" and shown["unit"] == "csv-export" and shown["project"] == "faker-kit" and shown["bot"] == FK_BOT and shown["card"] == csv["id"] and shown["title"] == csv["title"]
             and {"part", "state", "prs", "session"} <= set(shown) and "owner" not in shown and foreign["owner"] == foreign["route_to"] == FK_BOT and foreign["card"] == csv["id"]
             and from_chief["route_to"] == FK_BOT and missing[0] != 0 and "FK-99" in missing[2] and board.mutations() == mutations, json.dumps(foreign))

    wrong = {
        "loop close": kit("loop", "close", "FK-1", "--reason", "not mine", bot=FREN_BOT),
        "loop title": kit("loop", "title", "FK-1", "--text", "Not mine to name", bot=FREN_BOT),
        "session steer": kit("session", "steer", "FK-1", "--session", "session_abc", bot=FREN_BOT),
        "session summary": kit("session", "summary", "FK-1", bot=FREN_BOT),
    }
    run.step("card refs: loop close, loop title and session commands refuse a ref of another project with the bot to ask and change nothing (" + ", ".join(wrong) + ")",
             all(r[0] == 1 and "FK-1 belongs to the fk-bot bot; ask that bot" in r[2] for r in wrong.values()) and board.mutations() == mutations,
             " | ".join(f"{k}: {v[2].strip()[:70]}" for k, v in wrong.items()))
    own_close = made(("loop", "close", "fk-4", "--reason", "built by mistake"), FK_BOT)
    run.step("card refs: loop close takes a ref of the caller's own card", own_close["closed"] is True and own_close["card"] == after_close["card"], json.dumps(own_close))

    titled = made(("loop", "title", "fk-1", "--text", "Admins download the catalog as CSV"), FK_BOT)
    mutations = board.mutations()
    again = made(("loop", "title", "FK-1", "--text", "Admins download the catalog as CSV"), FK_BOT)
    edited = board.by_key()["faker-kit:faker-kit:csv-export"]
    bad = {
        "another project's ref": kit("loop", "title", "FK-1", "--text", "Someone else's", bot=FREN_BOT),
        "another project's card id": kit("loop", "title", csv["id"], "--text", "Someone else's", bot=FREN_BOT),
        "a phrase over the limit": kit("loop", "title", "FK-1", "--text", " ".join(["Words"] * 12), bot=FK_BOT),
        "no phrase": kit("loop", "title", "FK-1", bot=FK_BOT),
    }
    quiet = board.mutations() == mutations
    part_title = made(("loop", "title", "FK-3", "--text", "Cards carry a short reference"), CHIEF)
    run.step("card refs: loop title edits one title of an own card and keeps the ref in front, leaves the body alone, repeats as no edit, "
             "and refuses another project's card, a long phrase and no phrase",
             titled["title"] == "FK-1 Admins download the catalog as CSV" and edited["title"] == titled["title"] and edited["body"] == body_before and again["changed"] is False
             and quiet and all(r[0] != 0 for r in bad.values()) and "FK-1 belongs to the fk-bot bot" in bad["another project's ref"][2]
             and "not one" in bad["another project's card id"][2] and "at most" in bad["a phrase over the limit"][2] and part_title["title"] == "FK-3 Cards carry a short reference",
             json.dumps(titled))

    pal = made(("loop", "unit", "ref-format", "--title", "Cards show a short reference"), PAL_BOT)
    mutations = board.mutations()
    padded = [made(("loop", "show", text), PAL_BOT) for text in ("PAL-01", "pal-1", "pal-001", "Pal-1")]
    run.step("card refs: the stored ref has no padding (PAL-1), and PAL-01, pal-1, pal-001 and Pal-1 all resolve the same card",
             pal["ref"] == "PAL-1" and "Ref: PAL-1" in lines(board.by_key()["faker-kit:hermes-bot-mobile:ref-format"]) and all(p["card"] == pal["card"] and p["ref"] == "PAL-1" for p in padded)
             and board.mutations() == mutations, json.dumps([p["ref"] for p in padded]))


def card_backfill_refs_scenario(run: Run) -> None:
    board, kit = setup(run)
    fk = "faker-kit"
    old_card(board, "faker-kit:faker-kit:late-unit", fk, FK_BOT, "unit", 1799990300.0)
    old_card(board, "faker-kit:faker-kit:repo-setup", fk, FK_BOT, "unit", 1799990100.0)
    old_card(board, "faker-kit:faker-kit:same-time-b", fk, FK_BOT, "unit", 1799990200.0)
    old_card(board, "faker-kit:faker-kit:same-time-a", fk, FK_BOT, "unit", 1799990200.0)
    old_card(board, "faker-kit:faker-kit:old-done", fk, FK_BOT, "unit", 1799990000.0, state="merged", status="archived")
    old_card(board, "faker-kit:faker-kit:finished", fk, FK_BOT, "unit", 1799990010.0, status="done")
    old_card(board, "faker-kit:faker-kit:merged-open", fk, FK_BOT, "unit", 1799990020.0, state="merged")
    old_card(board, "faker-kit:faker-kit:archived-ref", fk, FK_BOT, "unit", 1799989500.0, state="abandoned", status="archived", Ref="FK-20")
    old_card(board, "faker-kit:faker-kit:has-ref", fk, FK_BOT, "unit", 1799989000.0, Ref="FK-7")
    old_card(board, "faker-kit:fren:other-project", "fren", FREN_BOT, "unit", 1799990000.0)
    old_card(board, "faker-kit:chief:11", fk, FK_BOT, "part", 1799990400.0, state="handed-out")
    old_card(board, "faker-kit:chief:12", "nopref", NOPREF_BOT, "part", 1799990500.0, state="handed-out")
    broken = board.add_card("faker-kit:faker-kit:broken", "broken", f"{FOOTER}\nKey: faker-kit:faker-kit:broken\nRef: nonsense\n", FK_BOT)
    first = {t["id"]: t for t in board.state()["tasks"]}
    before = json.dumps(board.state()["tasks"], sort_keys=True)
    mutations = board.mutations()

    dry = json_of(kit("loop", "backfill", "--refs", "--dry-run", bot=FK_BOT))
    planned = {c["key"].rsplit(":", 1)[1]: c for c in dry["cards"]}
    run.step("card refs backfill: the dry run lists card id, key and ref for each open own card without one in creation order (ties by card id), numbers after the highest ref any card holds "
             "(an archived FK-20 counts), skips the done, merged and archived cards as not open, keeps FK-7, lists the unreadable card, and writes nothing",
             [planned[k]["ref"] for k in ("repo-setup", "same-time-b", "same-time-a", "late-unit")] == ["FK-21", "FK-22", "FK-23", "FK-24"] and "has-ref" not in planned
             and sorted(c["key"].rsplit(":", 1)[1] for c in dry["skipped"]) == ["finished", "merged-open", "old-done"] and all(c["reason"] == "not open" for c in dry["skipped"])
             and not {"old-done", "finished", "merged-open", "archived-ref"} & set(planned)
             and "other-project" not in planned and planned["repo-setup"]["card"] == next(i for i, t in first.items() if t["title"] == "repo-setup") and dry["kept"] == 1
             and [u["card"] for u in dry["unparsed"]] == [broken] and dry["to_number"] == 4 and board.mutations() == mutations and json.dumps(board.state()["tasks"], sort_keys=True) == before,
             json.dumps(planned["repo-setup"]))
    cdry = json_of(kit("loop", "backfill", "--refs", "--dry-run", bot=CHIEF))
    run.step("card refs backfill: the chief's dry run numbers its part cards in the project's sequence and lists a part of a project with no prefix row, never inventing one",
             [c["ref"] for c in cdry["cards"]] == ["FK-21"] and len(cdry["no_prefix"]) == 1
             and "PREFIXES in hermes/lib/faker_kit_refs.py" in cdry["no_prefix"][0]["reason"] and board.mutations() == mutations,
             json.dumps(cdry["no_prefix"]))

    applied = json_of(kit("loop", "backfill", "--refs", "--apply", bot=FK_BOT))
    after = {t["id"]: t for t in board.state()["tasks"]}
    mutating = board.mutating()
    footer_only = all(after[i]["title"] == first[i]["title"] and (after[i]["body"] == first[i]["body"] or after[i]["body"].replace(f"Ref: {planned[k]['ref']}\n", "", 1) == first[i]["body"])
                      for k in planned for i in (planned[k]["card"],))
    left_alone = [board.by_key()[f"faker-kit:faker-kit:{k}"] for k in ("old-done", "finished", "merged-open")]
    run.step("card refs backfill: apply writes only the Ref line of the footer of open cards with edit calls (never create, archive, complete, "
             "claim or evidence); the body text and every title stay byte-identical, the done, merged and archived cards are untouched and get no ref, "
             "and cards that had a ref keep it",
             applied["changed"] == 4 and set(mutating) == {"edit"} and len(mutating) == 4 and footer_only
             and all(f"Ref: {planned[k]['ref']}" in lines(after[planned[k]["card"]]) for k in planned)
             and all(t["body"] == first[t["id"]]["body"] and "Ref:" not in " ".join(lines(t)) for t in left_alone) and left_alone[0]["status"] == "archived"
             and "Ref: FK-20" in lines(board.by_key()["faker-kit:faker-kit:archived-ref"])
             and "Ref: FK-7" in lines(board.by_key()["faker-kit:faker-kit:has-ref"])
             and board.by_key()["faker-kit:fren:other-project"]["body"] == first[board.by_key()["faker-kit:fren:other-project"]["id"]]["body"],
             json.dumps(applied)[:300])
    mutations = board.mutations()
    chief_applied = json_of(kit("loop", "backfill", "--refs", "--apply", bot=CHIEF))
    second = json_of(kit("loop", "backfill", "--refs", "--apply", bot=FK_BOT))
    third = json_of(kit("loop", "backfill", "--refs", "--apply", bot=CHIEF))
    quiet = board.mutations() == mutations + 1
    verbs = {v[0] for v in board.verbs()}
    fresh = json_of(kit("loop", "unit", "fresh-one", "--title", "Visitors see the new banner", bot=FK_BOT))
    run.step("card refs backfill: the chief numbers its part card after the project bot's (FK-25), a second apply by either bot changes nothing and issues no edit, "
             "and a card created afterwards takes the highest ref of the project, closed cards included, plus one (FK-26)",
             chief_applied["changed"] == 1 and "Ref: FK-25" in lines(board.by_key()["faker-kit:chief:11"]) and fresh["ref"] == "FK-26" and quiet and second["changed"] == 0 and third["changed"] == 0
             and second["to_number"] == 0, json.dumps([chief_applied["changed"], second["changed"], third["changed"]]))
    run.step("card refs backfill: across the whole scenario no call creates, archives, completes, claims, reclaims or records evidence",
             not verbs & {"complete", "claim", "reclaim", "evidence", "archive", "block", "create"}, ",".join(sorted(verbs)))
