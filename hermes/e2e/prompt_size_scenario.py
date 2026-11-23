from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from t3code_scenario import GOOD, claude_calls, dispatch, records

if TYPE_CHECKING:
    from run_dispatch_e2e import Run

HERE = Path(__file__).resolve().parent
BUDGET = json.loads((HERE / "prompt-budget.json").read_text())
LONG_BRIEF = (HERE / "fixtures" / "bot-brief.md").read_text().strip()
TEMPLATE = (HERE.parent / "templates" / "fix-issue-brief.md").read_text()
ONCE = {
    "andon": ("Stop rule:", "Label rule:", "Never read or print a secret or merge"),
    "closing summary": ("Closing summary rule:", "starting `Closing summary:`", "Never guess an open requirement", "list it under Open questions"),
    "CI": ("CI rule:", "never wait for CI"),
    "faker-mode rule": ("Faker-mode rule:",),
    "coding rule": ("Reuse before build:",),
}
PHRASES = (*(phrase for phrases in ONCE.values() for phrase in phrases), "Unit: <unit-id>")


def filled_template() -> str:
    values = {"number": "7", "repo": BUDGET["sample_repo"], "labeler": "octo", "url": "https://github.com/acme/shop/issues/7", "standing": "Never merge.", "models": ""}
    return TEMPLATE.format(**values).strip()


def preview(run: Run, name: str, text: str) -> tuple[dict, str]:
    brief = run.brief(name, text)
    code, out, err = run.kit("launch", "--check", "--show-prompt", "--brief-file", str(brief))
    return (json.loads(out) if out.strip() else {}), f"exit {code} {err.strip()}"


def table(report: dict) -> str:
    return ", ".join(f"{c['name']} {c['chars']}" for c in report["components"]) + f"; fixed {report['fixed_chars']}, total {report['total_chars']} (about {report['total_tokens']} tokens)"


def over_budget(report: dict) -> list[str]:
    limits = BUDGET["components"]
    found = [f"{c['name']} is {c['chars']} chars, budget {limits[c['name']]}" for c in report["components"] if c["name"] in limits and c["chars"] > limits[c["name"]]]
    if report["fixed_chars"] > BUDGET["fixed_footers_chars"]:
        found.append(f"fixed footers are {report['fixed_chars']} chars, budget {BUDGET['fixed_footers_chars']}")
    return found


def first_message(run: Run, backend: str, printed: dict) -> str:
    if backend == "t3code":
        return printed["call"]["args"]["message"]
    return json.loads(run.claude_log.read_text().splitlines()[-1])["argv"][1]


def backend_steps(run: Run, backend: str) -> None:
    dispatch(run, {"backend": "claude"} if backend == "claude" else GOOD)
    calls, prepared = claude_calls(run), len(records(run, "meo-beo"))
    for label, text in (("fix-issue template", filled_template()), ("bot brief", LONG_BRIEF)):
        name = f"size-{backend}-{label.split()[0]}.md"
        report, said = preview(run, name, text)
        prompt = report.get("prompt", "")
        parts = {c["name"]: c for c in report.get("components", [])}
        run.step(f"prompt size: {backend} --check --show-prompt prints the prompt and a size table for the {label}, starts nothing and claims nothing",
                 report.get("ok") is True and report.get("started") is False and report.get("backend") == backend and prompt.startswith(text)
                 and set(parts) == {"brief", *ONCE} and parts["brief"]["chars"] == len(text) and report["total_chars"] == len(prompt)
                 and claude_calls(run) == calls and len(records(run, "meo-beo")) == prepared, said or table(report))
        run.step(f"prompt size: {backend} fixed footers for the {label} stay within the budget", not over_budget(report) and report["fixed_chars"] < BUDGET["fixed_footers_chars"],
                 "; ".join(over_budget(report)) or table(report))
        run.step(f"prompt size: {backend} prompt for the {label} holds each fixed footer once", all(prompt.count(phrase) - text.count(phrase) == 1 for phrase in PHRASES), table(report))
    code, out, err = run.kit("launch", "--brief-file", str(run.brief(f"size-{backend}-real.md", LONG_BRIEF)))
    printed = json.loads(out) if code == 0 else {}
    sent = first_message(run, backend, printed) if code == 0 else ""
    expected, _ = preview(run, f"size-{backend}-real.md", LONG_BRIEF)
    run.step(f"prompt size: {backend} sends the session exactly the prompt the preview printed", code == 0 and sent == expected.get("prompt") and len(sent) == expected.get("total_chars"),
             err.strip() or f"{len(sent)} chars sent")


def notice_steps(run: Run) -> None:
    dispatch(run, {"backend": "claude"})
    long_text = LONG_BRIEF + "\n" + "x" * 200
    short_text = filled_template()
    shown, _ = preview(run, "notice-long.md", long_text)
    quiet, _ = preview(run, "notice-short.md", short_text)
    calls = claude_calls(run)
    code, out, err = run.kit("launch", "--brief-file", str(run.brief("notice-long-real.md", long_text)))
    launched = json.loads(out) if code == 0 else {}
    code_short, out_short, _ = run.kit("launch", "--brief-file", str(run.brief("notice-short-real.md", short_text)))
    notice = re.compile(r"brief is (\d+) chars; the usual brief is under 3,000: cut recited rules and hypotheses")
    found = notice.fullmatch(str(shown.get("notice", "")))
    run.step("prompt size: a brief over 6,000 characters prints the notice in --check and in launch, keeps ok true, and still starts the session",
             found is not None and int(found.group(1)) == len(long_text.strip()) > 6000 and shown.get("ok") is True and code == 0 and notice.fullmatch(str(launched.get("notice", ""))) is not None
             and claude_calls(run) == calls + 2, err.strip() or str(shown.get("notice")))
    run.step("prompt size: a short brief prints no notice in --check or in launch", "notice" not in quiet and code_short == 0 and "notice" not in json.loads(out_short), str(quiet.get("notice")))


def prompt_size_scenario(run: Run) -> None:
    for backend in ("claude", "t3code"):
        backend_steps(run, backend)
    notice_steps(run)
