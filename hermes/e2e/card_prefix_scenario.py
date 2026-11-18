from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from kanban_scenario import CHIEF, Board, json_of, lines

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

HERMES = Path(__file__).resolve().parent.parent
BOOTSTRAP = "ong-xay"
REGISTRY = "card-prefixes.json"
DECISIONS = "none: the work is obvious from the report"
PROJECTS = {"tms-bot": "titox-mobile-sdk", "leg-bot": "legacy-app", "fk-bot": "faker-kit"}
ADD_PROJECT_SCRIPT = (
    "import sys, io\n"
    "sys.path[:0] = sys.argv[1:4]\n"
    "import faker_kit\n"
    "raise SystemExit(faker_kit.main(sys.argv[4:], bot='ong-xay', stdin=io.BytesIO(b'')))\n"
)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def card_prefix_scenario(run: Run) -> None:
    extra = "".join(f"  {bot}:\n    role: project\n    project: {project}\n    repo: acme/{project}\n" for bot, project in PROJECTS.items())
    bots = run.team / "bots.yaml"
    bots.write_text(bots.read_text().replace("bots:\n", f"bots:\n  {CHIEF}:\n    role: chief\n", 1) + extra)
    (run.team / "kanban-cutover.json").write_text(json.dumps({"at": 1, "board": "default", "parts": [], "units": {}}))
    board = Board(run)
    registry = run.team / REGISTRY

    def add(project: str, bot: str) -> dict:
        run.update_gh(lambda state: state.update({"repo": f"acme/{project}"}))
        return json_of(run.kit("add-project", f"acme/{project}", "--bot", bot, "--project", project, bot=BOOTSTRAP))

    def table() -> dict:
        return json.loads(registry.read_text()) if registry.exists() else {}

    other_before = {k: v["body"] for k, v in board.by_key().items()}
    fk_unit = json_of(run.kit("loop", "unit", "csv-export", "--title", "Admins can export the catalog as CSV", bot="fk-bot"))
    fk_body = board.by_key()["faker-kit:faker-kit:csv-export"]["body"]
    run.step("card prefixes: a project the shipped table knows gets its card with no registry", fk_unit["ref"] == "FK-1" and not registry.exists() and not other_before, json.dumps(fk_unit["ref"]))

    tms_part = json_of(run.kit("handoff", "--project", "titox-mobile-sdk", "--part", "Let the owner try the SDK on a phone", "--decisions", DECISIONS, "--title", "SDK runs on a phone", bot=CHIEF))
    tms_unit = json_of(run.kit("loop", "unit", "sdk-start", "--title", "The SDK starts on a phone", bot="tms-bot"))
    shown = json_of(run.kit("loop", "show", "tms-1", bot="tms-bot"))
    run.step("card prefixes: titox-mobile-sdk gets TMS from the shipped table with no registry, its first card is TMS-1, and tms-1 resolves in any case",
             tms_part.get("ref") == "TMS-1" and tms_unit.get("ref") == "TMS-2" and shown.get("ref") == "TMS-1" and shown.get("card") == tms_part.get("card") and not registry.exists(),
             json.dumps([tms_part.get("ref"), tms_unit.get("ref"), shown.get("ref")]))

    aw = add("acme-widgets", "aw-bot")
    chief_card = json_of(run.kit("handoff", "--project", "acme-widgets", "--part", "Let the owner list the widgets", "--decisions", DECISIONS, "--title", "Widgets can be listed", bot=CHIEF))
    unit = json_of(run.kit("loop", "unit", "widget-list", "--title", "Widgets show in a list", bot="aw-bot"))
    cards = board.by_key()
    registered = registry.read_text()
    run.step("card prefixes: add-project derives AW for acme-widgets (initials of its words), prints it, "
             "and records it in card-prefixes.json mode 0600 with no other content",
             aw.get("card_prefix") == "AW" and table() == {"acme-widgets": "AW"} and stat.S_IMODE(registry.stat().st_mode) == 0o600
             and not [p for p in run.team.iterdir() if p.name.startswith(f".{REGISTRY}")], json.dumps([aw.get("card_prefix"), table(), oct(stat.S_IMODE(registry.stat().st_mode))]))
    run.step("card prefixes: the chief's handoff and the project bot's loop unit for a registry project both create a card "
             "(AW-1 then AW-2, one sequence per project) with the prefix in title and Ref line",
             chief_card.get("ref") == "AW-1" and unit.get("ref") == "AW-2" and cards["faker-kit:acme-widgets:widget-list"]["title"] == "AW-2 Widgets show in a list"
             and "Ref: AW-2" in lines(cards["faker-kit:acme-widgets:widget-list"]), json.dumps([chief_card.get("ref"), unit.get("ref")]))

    inode, before = registry.stat().st_ino, registry.read_text()
    again = add("acme-widgets", "aw-bot")
    run.step("card prefixes: running add-project again keeps the stored prefix and does not rewrite the registry",
             again.get("card_prefix") == "AW" and registry.read_text() == before == registered and registry.stat().st_ino == inode, json.dumps(again.get("card_prefix")))

    wares = add("acme-wares", "aw2-bot")
    kitchen = add("fast-kitchen", "fa-bot")
    tiny = add("tiny-mobile-sdk", "tm-bot")
    mine = table()
    run.step("card prefixes: a derivation that clashes with a registry prefix (acme-wares vs AW), a shipped one (fast-kitchen vs FK) "
             "or titox-mobile-sdk's TMS (tiny-mobile-sdk) gets a different 2 to 5 letter prefix",
             wares.get("card_prefix") not in (None, "AW") and kitchen.get("card_prefix") not in (None, "FK") and tiny.get("card_prefix") not in (None, "TMS")
             and len(set(mine.values()) | {"FK", "TMS", "PAL"}) == len(mine) + 3 and all(2 <= len(v) <= 5 and v.isalpha() and v.isupper() for v in mine.values()),
             json.dumps(mine, sort_keys=True))

    folders = [str(HERMES / folder) for folder in ("lib", "bin", "plugin")]
    pair = [subprocess.Popen([sys.executable, "-c", ADD_PROJECT_SCRIPT, *folders, "add-project", f"acme/{name}", "--bot", f"{name}-bot", "--project", name],
                             env=run.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for name in ("zeta-orb", "zen-orb", "zip-orb")]
    outs = [p.communicate() for p in pair]
    race = table()
    run.step("card prefixes: three add-project runs started together for projects that derive the same prefix get three different prefixes",
             all(p.returncode == 0 for p in pair) and all(race.get(n) for n in ("zeta-orb", "zen-orb", "zip-orb")) and len({race[n] for n in ("zeta-orb", "zen-orb", "zip-orb")}) == 3,
             json.dumps({n: race.get(n) for n in ("zeta-orb", "zen-orb", "zip-orb")}) + " " + " ".join(o[1].strip()[:80] for o in outs))

    legacy_before = {k: v["body"] for k, v in board.by_key().items()}
    stored = table()
    lazy = json_of(run.kit("loop", "unit", "legacy-start", "--title", "The legacy app starts", bot="leg-bot"))
    lazy_two = json_of(run.kit("loop", "unit", "legacy-next", "--title", "The legacy app opens a list", bot="leg-bot"))
    after = {k: v["body"] for k, v in board.by_key().items()}
    run.step("card prefixes: a project in bots.yaml with no prefix anywhere gets one at its first card, says so once, "
             "keeps it for the next card, and no existing card changes",
             lazy.get("ref") == "LA-1" and "prefix_registered" in lazy and lazy_two.get("ref") == "LA-2" and "prefix_registered" not in lazy_two
             and table() == {**stored, "legacy-app": "LA"} and all(after[k] == v for k, v in legacy_before.items()) and after["faker-kit:faker-kit:csv-export"] == fk_body,
             json.dumps([lazy.get("ref"), lazy.get("prefix_registered"), lazy_two.get("ref")]))

    registry.write_text('{"acme-widgets": "AW", "legacy-app"')
    corrupt = registry.read_text()
    mutations = board.mutations()
    refused = {
        "loop unit": run.kit("loop", "unit", "widget-two", "--title", "Widgets show in a grid", bot="aw-bot"),
        "loop show of a registry ref": run.kit("loop", "show", "aw-1", bot="aw-bot"),
        "add-project": run.kit("add-project", "acme/new-thing", "--bot", "nt-bot", "--project", "new-thing", bot=BOOTSTRAP),
        "handoff": run.kit("handoff", "--project", "acme-widgets", "--part", "Let the owner sort the widgets", "--decisions", DECISIONS, "--title", "Widgets can be sorted", bot=CHIEF),
    }
    unchanged = board.mutations() == mutations
    known = json_of(run.kit("loop", "unit", "csv-two", "--title", "Admins can export the catalog as XML", bot="fk-bot"))
    run.step("card prefixes: a corrupt registry fails closed for every command that needs it, naming the file, creating nothing and keeping its content; a shipped prefix still works ("
             + ", ".join(refused) + ")",
             all(r[0] != 0 and REGISTRY in r[2] for r in refused.values()) and registry.read_text() == corrupt and unchanged and known.get("ref") == "FK-2"
             and "nt-bot" not in (run.team / "bots.yaml").read_text(), " | ".join(f"{k}: {v[2].strip()[:90]}" for k, v in refused.items()))
    registry.write_text(json.dumps({"acme-widgets": "AW"}))
    repaired = json_of(run.kit("loop", "unit", "widget-two", "--title", "Widgets show in a grid", bot="aw-bot"))
    run.step("card prefixes: after the owner repairs the file the same command works again", repaired.get("ref") == "AW-3", digest(registry.read_text()))
