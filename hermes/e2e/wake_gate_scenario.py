from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

from gate_scenario import QUIET, wakes

BABYSIT = "faker-kit-babysit"
LOOP = "faker-kit-loop"
NEWS_JOB = "faker-kit-chief-news"
HEARTBEAT = 6 * 60 * 60
MARGIN = 120
NOT_RECORDED = "no earlier tick of this gate is recorded"
CHANGED = "the status changed"
NOTICE_REASON = "a faker-kit notice printed"
HEARTBEAT_REASON = "six hours passed since this gate last woke the agent while a pull request is open"
LAST_GONE = "the last open pull request merged or closed"
STUCK_REASON = "a unit has no pull request past its timebox"
RUN_FAILED = "the job's previous run after the last wake did not finish ok"
SHAPE = "faker-kit loop --all printed an unexpected report shape"
NO_PRS_PART = {"id": 1, "project": "shop", "state": "waiting", "waits_on": None}
LOOP_REASON = "a waiting part is due or a part has news for the owner"
ISSUES_REASON = "faker-kit issues lists 1 issue(s) to handle"
NO_PULLS = "no open pull requests"
NOTICE = "faker-kit notice: something the kit's own code reports"
STATUS = [
    f"#1 | unit csv | by alice | ready | labeled | mergeable | updated 2026-11-09T01:00:00Z | head {'a' * 40} | ready to merge",
    f"#2 | by bob | draft | labeled | conflicts | updated 2026-11-09T02:00:00Z | head {'b' * 40} | blocked: checks have not all passed (0 failed, 1 pending)",
]
UNLABELED_GATE = "blocked: not labeled faker-kit; autonomous bots leave it alone, only the owner merges it"
STRANGER = f"#7 | by carol | ready | unlabeled | mergeable | updated 2026-11-09T03:00:00Z | head {'c' * 40} | {UNLABELED_GATE}"
LABELED = {"number": 11, "labeler": "alice", "url": "https://github.com/acme/shop/issues/11", "event": 101}
REAL_LABELED = {"number": 21, "labeler": "alice", "url": "https://github.com/acme/shop/issues/21", "event": 301}
FINGERPRINT = re.compile(r"\A[0-9a-f]{64}\Z")
SHORT_STDERR = 500
READY_QUIET = {"dry_run": False, "result": "unchanged", "marked": [], "failed": [], "lines": []}
CONFLICTS_QUIET = {"dry_run": False, "open": [], "conflicts": []}
RECONCILE_QUIET = {"mode": "apply", "read": 0, "entries": {"merged": [], "closed": [], "unchanged": [], "published": [], "unreadable": [], "refused": [],
                                                           "completion_pending": [], "stale_blocked": []}}


class Run(Protocol):
    def gh_now(self) -> dict: ...
    def update_gh(self, change: Callable[[dict], object]) -> None: ...
    def step(self, name: str, ok: bool, evidence: str) -> None: ...


def fingerprint_of(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def issues_json(rows: list[dict]) -> str:
    return json.dumps({"issues": rows}, sort_keys=True)


def iso(moment: float) -> str:
    return datetime.fromtimestamp(moment).astimezone().isoformat()


def summary_lines(done: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in done.stdout.splitlines() if line.startswith("Closing summaries to read:")]


def shown(done: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in done.stdout.splitlines() if not line.startswith("Closing summaries to read:")]


def part(number: int, state: str, **fields: object) -> dict:
    return {"id": number, "project": "shop", "bot": "meo-beo", "part": f"part {number}", "state": state, "prs": [], "waits_on": None,
            "added": 1.0, "updated": 1.0, "reported": None, **fields}


def loop_json(parts: list[dict], to_report: list[dict] | None = None) -> str:
    return json.dumps({"open": parts, "to_report": to_report or []}, sort_keys=True)


class Gates:
    def __init__(self, run: Run, hermes: Path, bot: str, chief: str, env: dict[str, str], work: Path) -> None:
        self.run, self.bot, self.profile, self.hermes = run, bot, hermes / "profiles" / bot, hermes
        self.chief_profile = hermes / "profiles" / chief
        self.chief_name = chief
        self.runs_before = 0
        self.wrapper = hermes / "bin" / "faker-kit"
        self.gates = Path(env["FAKER_KIT_HOME"]) / "gates"
        self.scripts = {BABYSIT: self.profile / "scripts" / "faker-kit-babysit-gate.py", LOOP: self.chief_profile / "scripts" / "faker-kit-loop-gate.py"}
        self.states = {BABYSIT: self.gates / bot / f"{BABYSIT}.json"}
        self.jobs_file = self.profile / "jobs.json"
        self.hermes_bins = [Path(folder) / "hermes" for folder in env["PATH"].split(":") if (Path(folder) / "hermes").is_file()]
        self.fake = work / "wake-fake"
        self.fake.mkdir()

    def tick(self, job: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {"PATH": "/usr/bin:/bin", "HERMES_HOME": str(home or self.scripts[job].parent.parent)}
        return subprocess.run([sys.executable, str(self.scripts[job])], env=env, cwd=self.hermes, capture_output=True, text=True, timeout=120, check=False)

    def snapshot(self, job: str) -> tuple[bytes, int] | None:
        path = self.states[job]
        return (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None

    def state(self, job: str) -> dict:
        return json.loads(self.states[job].read_text())

    def set_woke_at(self, job: str, woke_at: float, retries: int | None = None) -> None:
        self.states[job].write_text(json.dumps({**self.state(job), "woke_at": woke_at, **({} if retries is None else {"retries": retries})}))

    def set_last_run(self, outcome: str | None, at: float | None = None) -> None:
        jobs = json.loads(self.jobs_file.read_text())
        for job in jobs:
            if job["name"] == BABYSIT:
                job.pop("last_run", None)
                if outcome is not None:
                    job["last_run"] = f"{iso(at if at is not None else time.time())}  {outcome}"
        self.jobs_file.write_text(json.dumps(jobs, sort_keys=True))

    def hermes_lists(self) -> int:
        log = self.hermes / "hermes-calls.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
        return sum(1 for call in calls if call == ["-p", self.bot, "cron", "list", "--all"])

    def reset(self) -> None:
        shutil.rmtree(self.gates, ignore_errors=True)

    @staticmethod
    def say(done: subprocess.CompletedProcess[str]) -> str:
        return (done.stdout.strip() or done.stderr.strip())[:160]

    @staticmethod
    def woke(done: subprocess.CompletedProcess[str], reason: str) -> bool:
        first = done.stdout.splitlines()[0] if done.stdout else ""
        return done.returncode == 0 and wakes(done.stdout) and first.startswith("The ") and first.endswith(f" gate woke this tick because {reason}.")

    @staticmethod
    def silent(done: subprocess.CompletedProcess[str]) -> bool:
        return done.returncode == 0 and done.stdout == QUIET + "\n" and not wakes(done.stdout)

    @staticmethod
    def failed(done: subprocess.CompletedProcess[str], message: str) -> bool:
        return done.returncode != 0 and done.stdout == "" and done.stderr.startswith(message) and len(done.stderr) < SHORT_STDERR

    def program(self, command: str, out: str | None = None, code: int = 0, err: str = "") -> None:
        for suffix, text in (("out", out), ("err", err or None), ("code", str(code) if code else None)):
            target = self.fake / f"{command}.{suffix}"
            target.unlink(missing_ok=True)
            if text is not None:
                target.write_text(text)

    def serve(self, status: list[str] | str, issues: str | None = None) -> None:
        self.program("status", status if isinstance(status, str) else "\n".join(status) + "\n")
        self.program("issues", issues if issues is not None else issues_json([]))
        self.program("news", json.dumps({"tracked": []}))
        self.program("loop-check", json.dumps({"findings": [], "lines": [], "ok": True, "needs_action": False}))
        self.program("loop-reconcile", json.dumps(RECONCILE_QUIET))
        self.program("loop-ready", json.dumps(READY_QUIET))
        self.program("session-conflicts", json.dumps(CONFLICTS_QUIET))

    def calls(self) -> list[str]:
        log = self.fake / "calls.log"
        return log.read_text().splitlines() if log.is_file() else []

    @contextmanager
    def fake_wrapper(self) -> Iterator[None]:
        original, mode = self.wrapper.read_bytes(), self.wrapper.stat().st_mode
        (self.fake / "calls.log").unlink(missing_ok=True)
        self.wrapper.write_text("\n".join([
            "#!/bin/sh", f"d={self.fake}", 'printf "%s\\n" "$*" >> "$d/calls.log"',
            '[ -f "$d/$1-$2.out" ] && { cat "$d/$1-$2.out"; exit 0; }', '[ -f "$d/$1.err" ] && cat "$d/$1.err" >&2', '[ -f "$d/$1.out" ] && cat "$d/$1.out"',
            '[ -f "$d/$1.code" ] && exit "$(cat "$d/$1.code")"', "exit 0", ""]))
        self.wrapper.chmod(mode)
        try:
            yield
        finally:
            self.wrapper.write_bytes(original)
            self.wrapper.chmod(mode)

    def real_command(self) -> None:
        step, update, now = self.run.step, self.run.update_gh, self.run.gh_now
        self.reset()
        self.set_last_run(None)
        held = {key: now()[key] for key in ("bugs", "events", "permissions", "pulls")}
        update(lambda state: state.update(bugs=[], events={}, permissions={}))
        first = self.tick(BABYSIT)
        stored = self.state(BABYSIT) if self.states[BABYSIT].is_file() else {}
        step("the babysit gate runs the installed faker-kit issues and status: with pull requests open its first tick wakes the agent with the real lines and records their fingerprint",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[1:3] == ["faker-kit issues:", issues_json([])] and first.stdout.splitlines()[4].startswith("#")
             and " | updated 20" in first.stdout and " | head " in first.stdout and bool(FINGERPRINT.match(str(stored.get("fingerprint")))), self.say(first))
        lists = self.hermes_lists()
        again = self.tick(BABYSIT)
        step("the real status prints the same lines on the next tick, so the gate skips it after reading the job's last run once from hermes cron list",
             self.silent(again) and self.hermes_lists() == lists + 1, self.say(again))
        shown = next(pull for pull in now()["pulls"] if pull["state"] == "open")

        def mark(**fields: object) -> Callable[[dict], object]:
            return lambda state: next(pull for pull in state["pulls"] if pull["number"] == shown["number"]).update(fields)

        update(mark(updated_at="2026-11-10T09:30:00Z"))
        commented = self.tick(BABYSIT)
        healthy = not first.stdout.splitlines()[4].split(" | ")[-1].startswith("blocked:")
        step("a comment or review on an open pull request moves only its updated time in the real status: a healthy line stays quiet, a blocked one wakes the gate with the new time",
             self.silent(commented) if healthy else self.woke(commented, CHANGED) and "updated 2026-11-10T09:30:00Z" in commented.stdout, self.say(commented))
        update(mark(mergeable=False))
        moved = self.tick(BABYSIT)
        update(mark(mergeable=shown["mergeable"], updated_at="2026-10-01T00:00:00Z"))
        step("a pull request that starts to conflict changes the real status, which wakes the babysit gate", self.woke(moved, CHANGED) and "conflicts" in moved.stdout, self.say(moved))

        self.tick(BABYSIT)
        self.set_woke_at(BABYSIT, time.time() - 600)
        self.set_last_run("error: the model provider refused the turn", time.time() - 300)
        retried = self.tick(BABYSIT)
        after = self.tick(BABYSIT)
        step("a woken turn that ended in error is retried once by the real gate even when the real status is unchanged, and the tick after it is quiet",
             self.woke(retried, RUN_FAILED) and self.silent(after), self.say(retried))
        self.set_last_run(None)

        update(lambda state: [pull.update(state="closed") for pull in state["pulls"]])
        emptied = self.tick(BABYSIT)
        stored = self.state(BABYSIT)
        step("with every pull request closed the real status prints no open pull requests and the gate wakes once with the last-pull-request reason, recording that as the new fingerprint",
             self.woke(emptied, LAST_GONE) and stored["fingerprint"] == fingerprint_of([NO_PULLS]), self.say(emptied))
        self.set_woke_at(BABYSIT, time.time() - HEARTBEAT - 3600)
        kept = self.snapshot(BABYSIT)
        late = self.tick(BABYSIT)
        step("seven hours later with still no pull request open the real gate stays quiet: no heartbeat, and nothing is written", self.silent(late) and self.snapshot(BABYSIT) == kept, self.say(late))

        bug = {"number": 21, "state": "open", "labels": ["faker:fix"], "title": "t", "body": "b"}
        label = {"id": 301, "event": "labeled", "label": {"name": "faker:fix"}, "actor": {"login": "alice"}}
        update(lambda state: state.update(bugs=[bug], events={"21": [label]}, permissions={"alice": {"permission": "write", "role_name": "write"}}))
        listed = self.tick(BABYSIT)
        update(lambda state: state.update(bugs=[], events={}, permissions={}, pulls=held["pulls"]))
        step("an issue a collaborator labeled makes the real faker-kit issues list it, which wakes the gate with the listing even with no pull request open",
             self.woke(listed, ISSUES_REASON) and listed.stdout.splitlines()[2] == issues_json([REAL_LABELED]), self.say(listed))
        reopened = self.tick(BABYSIT)
        step("a pull request that opens after a quiet stretch counts as a change and wakes the real gate", self.woke(reopened, CHANGED), self.say(reopened))
        update(lambda state: state.update(held))
        self.reset()

    def summaries(self) -> None:
        step = self.run.step

        def pull(number: int, unit: str, head: str, kind: str = "ready", gate: str = "ready to merge") -> str:
            return f"#{number} | unit {unit} | by alice | {kind} | labeled | mergeable | updated 2026-11-09T01:00:00Z | head {head * 40} | {gate}"

        self.reset()
        self.set_last_run(None)
        self.serve([pull(1, "alpha", "a")])
        opened = self.tick(BABYSIT)
        lines = summary_lines(opened)
        step("summary gate: a wake for an opened pull request carries one line naming the unit and head, telling the agent to run session summary first, and calling it a claim",
             self.woke(opened, NOT_RECORDED) and len(lines) == 1 and "unit alpha (#1, head aaaaaaa, ready)" in lines[0] and "faker-kit session summary <unit>" in lines[0]
             and "before you update its card, decide the next step, or report to the chief" in lines[0] and "never proof" in lines[0]
             and "verify the pull request, head, checks, and artifact on GitHub" in lines[0],
             self.say(opened))
        quiet = self.tick(BABYSIT)
        step("summary gate: a quiet tick stays silent with the exact skip line, so the unseen-summary line never starts a model turn on its own", self.silent(quiet), self.say(quiet))
        self.set_woke_at(BABYSIT, time.time() - HEARTBEAT - 60)
        heartbeat = self.tick(BABYSIT)
        step("summary gate: the same unit at the same head in a later wake (a heartbeat) adds no line", self.woke(heartbeat, HEARTBEAT_REASON) and not summary_lines(heartbeat), self.say(heartbeat))
        self.serve([pull(1, "alpha", "b", "draft", "blocked: draft")])
        drafted = self.tick(BABYSIT)
        step("summary gate: the pull request going to draft at a new head adds one line again, marked draft", self.woke(drafted, CHANGED) and len(summary_lines(drafted)) == 1
        and "(#1, head bbbbbbb, draft)" in summary_lines(drafted)[0], self.say(drafted))
        self.serve([pull(2, "beta", "c")])
        gone = self.tick(BABYSIT)
        wanted = summary_lines(gone)[0] if summary_lines(gone) else ""
        step("summary gate: a pull request that merged or closed is named once as ended with the head it last had, next to the unit whose pull request just opened, and the line stays one line",
             self.woke(gone, CHANGED) and len(summary_lines(gone)) == 1 and "unit alpha (#1, head bbbbbbb, ended)" in wanted and "unit beta (#2, head ccccccc, ready)" in wanted, self.say(gone))
        self.serve([pull(2, "beta", "c")])
        again = self.tick(BABYSIT)
        step("summary gate: that event again is quiet and adds nothing", self.silent(again) and not summary_lines(again), self.say(again))
        self.reset()

    def babysit(self) -> None:
        step = self.run.step
        self.reset()
        self.set_last_run(None)
        self.serve(STATUS)
        first = self.tick(BABYSIT)
        stored = self.state(BABYSIT) if self.states[BABYSIT].is_file() else {}
        folder = self.states[BABYSIT].parent
        step("the babysit gate's first tick with a pull request open wakes the agent with the issues JSON and the status lines as its Script Output, after one faker-kit issues and one status",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[1:6] == ["faker-kit issues:", issues_json([]), "faker-kit status:", *STATUS]
             and first.stdout.splitlines()[6].startswith("Closing summaries to read: unit csv (#1, head aaaaaaa, ready)") and len(first.stdout.splitlines()) == 7
             and self.calls() == ["issues", "loop reconcile", "loop ready", "session conflicts", "pickup status --no-job", "status", "news", "loop check", "loop show"], self.say(first))
        step("it stores the status fingerprint and the wake time as an owner-only file, with no temp file left beside it",
             set(stored) == {"fingerprint", "woke_at", "retries", "pulls", "asked", "units", "stuck"} and stored["asked"] == [f"csv|{'a' * 40}|ready"]
             and stored["retries"] == 0 and stored["stuck"] == []
             and set(stored["pulls"]) == {"#1", "#2"} and stored["fingerprint"] == fingerprint_of(STATUS)
             and bool(FINGERPRINT.match(stored["fingerprint"]))
             and abs(stored["woke_at"] - time.time()) < MARGIN and stat.S_IMODE(self.states[BABYSIT].stat().st_mode) == 0o600
             and stat.S_IMODE(folder.stat().st_mode) == 0o700 and [p.name for p in folder.iterdir()] == [self.states[BABYSIT].name], json.dumps(stored))

        kept = self.snapshot(BABYSIT)
        lists = self.hermes_lists()
        unchanged = self.tick(BABYSIT)
        step("an unchanged status prints the skip gate as its last line, exits 0, and writes nothing, after reading the job's last run once",
             self.silent(unchanged) and self.snapshot(BABYSIT) == kept and self.hermes_lists() == lists + 1, self.say(unchanged))
        self.serve("\n\n" + "\n".join(f"  {line}   " for line in STATUS) + "\n\n")
        spaced = self.tick(BABYSIT)
        step("a status that differs only in blank lines and surrounding spaces is the same signal and stays quiet", self.silent(spaced) and self.snapshot(BABYSIT) == kept, self.say(spaced))

        commented = [STATUS[0].replace("2026-11-09T01:00:00Z", "2026-11-10T03:00:00Z"), STATUS[1]]
        self.serve(commented)
        kept = self.snapshot(BABYSIT)
        touched = self.tick(BABYSIT)
        step("healthy progress wakes nothing: a pull request whose only difference is its updated time, with the same head and a gate that is not blocked, stays quiet and writes nothing",
             self.silent(touched) and self.snapshot(BABYSIT) == kept, self.say(touched))

        changed = [commented[0], commented[1].replace("pending", "failed")]
        self.serve(changed)
        woken = self.tick(BABYSIT)
        step("a changed status wakes the agent with only the changed pull request's line and stores the new fingerprint",
             self.woke(woken, CHANGED) and shown(woken)[4:] == [changed[1]] and self.state(BABYSIT)["fingerprint"] == fingerprint_of(changed), self.say(woken))
        self.news_steps(changed)

        self.serve([*changed, NOTICE])
        notice_first, notice_again = self.tick(BABYSIT), self.tick(BABYSIT)
        step("a faker-kit notice line wakes the agent and is printed verbatim, and the same notice wakes it again on the next tick",
             self.woke(notice_first, NOTICE_REASON) and self.woke(notice_again, NOTICE_REASON) and NOTICE in notice_again.stdout.splitlines(), self.say(notice_again))

        self.serve(changed)
        self.tick(BABYSIT)
        self.set_woke_at(BABYSIT, time.time() - HEARTBEAT + MARGIN)
        before = self.snapshot(BABYSIT)
        inside = self.tick(BABYSIT)
        step("a status unchanged for just under six hours since the last wake stays quiet and writes nothing", self.silent(inside) and self.snapshot(BABYSIT) == before, self.say(inside))
        self.set_woke_at(BABYSIT, time.time() - HEARTBEAT - MARGIN)
        elapsed = self.tick(BABYSIT)
        step("an unchanged status with a pull request open wakes the agent once six hours have passed since the last wake, and stores the new wake time",
             self.woke(elapsed, HEARTBEAT_REASON) and abs(self.state(BABYSIT)["woke_at"] - time.time()) < MARGIN, self.say(elapsed))
        self.set_woke_at(BABYSIT, time.time() + HEARTBEAT)
        skewed = self.tick(BABYSIT)
        step("a wake time in the future, as after a clock change, wakes the agent instead of keeping it quiet for good",
             self.woke(skewed, HEARTBEAT_REASON) and self.state(BABYSIT)["woke_at"] < time.time() + MARGIN, self.say(skewed))
        self.quiet_without_pulls()
        self.unlabeled_pulls()
        self.failed_runs(changed)
        self.damaged_state()
        self.failing_commands()

    def news_steps(self, settled: list[str]) -> None:
        step = self.run.step
        self.reset()
        newer = STATUS[0].replace("2026-11-09T01:00:00Z", "2026-11-10T05:00:00Z")
        self.serve([STATUS[0], STATUS[1], STATUS[0], newer])
        first = self.tick(BABYSIT)
        step("status lines for one pull request collapse to the last one and exact duplicates to one, so the agent sees one line per pull request and the fingerprint covers the collapsed lines",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[4:6] == [newer, STATUS[1]]
             and first.stdout.splitlines()[6].startswith("Closing summaries to read: unit csv") and self.state(BABYSIT)["fingerprint"] == fingerprint_of([newer, STATUS[1]]), self.say(first))
        blocker = f"#3 | by dana | ready | labeled | mergeable | updated 2026-11-10T06:00:00Z | head {'d' * 40} | blocked: a person requested changes"
        self.serve([newer, STATUS[1], blocker])
        opened = self.tick(BABYSIT)
        step("a new blocker wakes the agent with its one line and not the lines of the pull requests that did not change",
             self.woke(opened, CHANGED) and shown(opened)[4:] == [blocker], self.say(opened))
        touched_blocker = blocker.replace("2026-11-10T06:00:00Z", "2026-11-10T07:00:00Z")
        self.serve([newer, STATUS[1], touched_blocker])
        retouched = self.tick(BABYSIT)
        step("a blocked pull request whose updated time moved is a changed blocker and wakes the agent",
             self.woke(retouched, CHANGED) and shown(retouched)[4:] == [touched_blocker], self.say(retouched))
        self.serve([newer, STATUS[1], touched_blocker.replace("2026-11-10T07:00:00Z", "2026-11-10T08:00:00Z").replace("blocked: a person requested changes", "ready to merge")])
        unblocked = self.tick(BABYSIT)
        step("a blocker that clears is a changed pull request and wakes the agent with its new line",
             self.woke(unblocked, CHANGED) and shown(unblocked)[4:] == [self.served_last()], self.say(unblocked))
        self.serve([newer, STATUS[1]])
        completed = self.tick(BABYSIT)
        step("a pull request that was in the stored status and is gone wakes the agent with one completion line and nothing else",
             self.woke(completed, CHANGED) and shown(completed)[4:] == ["#3 merged or closed"], self.say(completed))
        kept = self.snapshot(BABYSIT)
        again = self.tick(BABYSIT)
        step("an unchanged tick after that is quiet and writes nothing", self.silent(again) and self.snapshot(BABYSIT) == kept, self.say(again))
        stored = self.state(BABYSIT)
        self.states[BABYSIT].write_text(json.dumps({"fingerprint": fingerprint_of(["#9 | old"]), "woke_at": stored["woke_at"], "retries": 0}))
        legacy = self.tick(BABYSIT)
        step("a state file with no per-pull-request fingerprints reads as changed once: the agent wakes with every line, and the file gains them",
             self.woke(legacy, CHANGED) and legacy.stdout.splitlines()[4:6] == [newer, STATUS[1]] and len(summary_lines(legacy)) == 1
             and set(self.state(BABYSIT)["pulls"]) == {"#1", "#2"}, self.say(legacy))
        self.serve(settled)

    def served_last(self) -> str:
        return (self.fake / "status.out").read_text().splitlines()[-1]

    def unlabeled_pulls(self) -> None:
        step = self.run.step
        self.reset()
        self.serve([STRANGER])
        opening = self.tick(BABYSIT)
        step("with only an unlabeled pull request open the gate stays quiet and records the status as no open pull requests",
             self.silent(opening) and self.state(BABYSIT)["fingerprint"] == fingerprint_of([NO_PULLS]), self.say(opening))
        self.set_woke_at(BABYSIT, time.time() - 7 * 60 * 60)
        kept = self.snapshot(BABYSIT)
        late = self.tick(BABYSIT)
        step("seven hours later with only an unlabeled pull request open the gate still stays quiet: no heartbeat, nothing written",
             self.silent(late) and self.snapshot(BABYSIT) == kept, self.say(late))
        self.serve([STRANGER, STATUS[0]])
        appeared = self.tick(BABYSIT)
        step("a labeled pull request that opens beside the unlabeled one wakes the gate, and the agent is shown only the labeled line",
             self.woke(appeared, CHANGED) and shown(appeared)[4:] == [STATUS[0]] and self.state(BABYSIT)["fingerprint"] == fingerprint_of([STATUS[0]]), self.say(appeared))
        self.serve([STRANGER.replace("2026-11-09T03:00:00Z", "2026-11-10T04:00:00Z"), STATUS[0]])
        stranger_moved = self.tick(BABYSIT)
        step("a change to an unlabeled pull request's line wakes nobody", self.silent(stranger_moved), self.say(stranger_moved))
        self.serve([STRANGER, STATUS[0]])
        self.tick(BABYSIT)
        self.serve([STATUS[0]])
        gone = self.tick(BABYSIT)
        step("when the only unlabeled pull request goes away the labeled set is unchanged and the gate stays quiet", self.silent(gone), self.say(gone))
        self.serve([STRANGER])
        last = self.tick(BABYSIT)
        again = self.tick(BABYSIT)
        step("when the last labeled pull request goes away while an unlabeled one remains, the gate wakes once with the last-pull-request reason and is then quiet",
             self.woke(last, LAST_GONE) and self.silent(again) and self.state(BABYSIT)["fingerprint"] == fingerprint_of([NO_PULLS]), self.say(last))
        self.serve([STRANGER.replace(" | unlabeled | ", " | labeled | ")])
        labeled_later = self.tick(BABYSIT)
        step("when the label is added to that pull request it counts, and the gate wakes", self.woke(labeled_later, CHANGED), self.say(labeled_later))
        self.serve([STRANGER, NOTICE])
        noticed = self.tick(BABYSIT)
        step("a notice line wakes the gate even when only unlabeled pull requests are open, and the unlabeled line is not shown",
             self.woke(noticed, NOTICE_REASON) and STRANGER not in noticed.stdout and NOTICE in noticed.stdout.splitlines(), self.say(noticed))
        unreadable = "faker-kit: refused: GitHub says Bad credentials"
        self.serve([unreadable])
        problem = self.tick(BABYSIT)
        step("a status line the gate cannot classify is kept and counts", self.woke(problem, CHANGED) and unreadable in problem.stdout.splitlines(), self.say(problem))

    def card_check(self) -> None:
        step = self.run.step
        self.reset()
        self.serve([NO_PULLS])
        quiet = self.tick(BABYSIT)
        line = "t_1abb3883 unit kanban-visual-mockup: PR #153 draft open, card ready, no run; fix: faker-kit loop reconcile"
        self.program("loop-check", json.dumps({"findings": [{"line": line, "needs_action": True}], "lines": [line], "ok": False, "needs_action": True}))
        loud = self.tick(BABYSIT)
        added = [row for row in loud.stdout.splitlines() if row.startswith("faker-kit loop check:")]
        step("card check gate: with nothing wrong loop check adds nothing and the tick stays quiet; a card that does not show the work wakes it with exactly one line naming the card and its fix",
             self.silent(quiet) and wakes(loud.stdout) and len(added) == 1 and line in added[0], self.say(loud))
        partial = {"ok": False, "partial": True, "candidates": 40, "reached": 12, "unreached": ["t_9", "t_10"]}
        self.program("loop-check", json.dumps({"findings": [], "lines": [], "needs_action": False, **partial}))
        unread = self.tick(BABYSIT)
        step("card check gate: a partial loop check with nothing needing action is no news, never a clean result: the tick stays quiet and prints no loop check line",
             self.silent(unread) and not [row for row in unread.stdout.splitlines() if row.startswith("faker-kit loop check:")], self.say(unread))
        self.program("loop-check", json.dumps({"findings": [{"line": line, "needs_action": True}], "lines": [line], "needs_action": True, **partial}))
        half = self.tick(BABYSIT)
        said = [row for row in half.stdout.splitlines() if row.startswith("faker-kit loop check:")]
        step("card check gate: a partial loop check that did find a card needing action still wakes the gate with that line and says how many cards the pass did not reach",
             wakes(half.stdout) and len(said) == 1 and line in said[0] and "partial pass, 2 card(s) not reached yet" in said[0], self.say(half))
        self.program("loop-check", json.dumps({"findings": [], "lines": [], "ok": True, "needs_action": False}))
        self.reset()
        self.card_events()

    def ready_gate(self) -> None:
        step = self.run.step
        self.reset()
        self.serve(STATUS)
        self.tick(BABYSIT)
        line = f"marked #5 ready for review (unit csv-two, head {'e' * 12})"
        self.program("loop-ready", json.dumps({**READY_QUIET, "result": "marked", "marked": [{"pr": 5, "unit": "csv-two", "head": "e" * 12}], "lines": [line]}))
        (self.fake / "calls.log").unlink(missing_ok=True)
        loud = self.tick(BABYSIT)
        again = self.tick(BABYSIT)
        calls = self.calls()
        step("ready gate: after the unit cards settle the tick runs faker-kit loop ready with no model turn of its own; a flipped pull request wakes the agent with exactly its line, once",
             self.woke(loud, line) and loud.stdout.splitlines().count(line) == 1 and calls.index("loop reconcile") < calls.index("loop ready") and self.silent(again), self.say(loud))
        self.program("loop-ready", "", 1, "boom")
        broken = self.tick(BABYSIT)
        broken_again = self.tick(BABYSIT)
        step("ready gate: a loop ready that cannot run prints one line once, the rest of the tick still runs, and the next tick is quiet",
             wakes(broken.stdout) and "faker-kit loop ready could not check the draft unit pull requests this tick" in broken.stdout and "loop check" in self.calls()
             and "loop show" in self.calls() and self.silent(broken_again), self.say(broken))
        failed = {**READY_QUIET, "failed": [{"pr": 6, "unit": "csv-three", "head": "f" * 12, "reason": "GitHub refused"}]}
        self.program("loop-ready", json.dumps(failed))
        refused = self.tick(BABYSIT)
        refused_again = self.tick(BABYSIT)
        step("ready gate: a flip GitHub refused is named once with its reason and retried by the next tick without repeating the line",
             wakes(refused.stdout) and "faker-kit loop ready: could not mark #6 ready for review (GitHub refused); the next tick retries" in refused.stdout and self.silent(refused_again),
             self.say(refused))
        self.program("loop-ready", json.dumps(READY_QUIET))
        self.reset()

    def pickup(self) -> None:
        step = self.run.step

        def report(rows: list[dict], enabled: bool = True) -> None:
            self.program("pickup-status", json.dumps({"enabled": enabled, "candidates": {"shop": len(rows)}, "pickup": rows}))

        self.reset()
        self.serve([NO_PULLS])
        report([], True)
        nothing = self.tick(BABYSIT)
        report([{"unit": "fix-search", "card": "t_41"}, {"unit": "add-shortcut", "card": "t_42"}], False)
        off = self.tick(BABYSIT)
        step("pickup gate: nothing to pick up, or pickup off with candidates present, prints nothing and adds no model turn", self.silent(nothing) and self.silent(off), self.say(off))
        report([{"unit": "fix-search", "card": "t_41"}, {"unit": "add-shortcut", "card": "t_42"}], True)
        loud = self.tick(BABYSIT)
        lines = [row for row in loud.stdout.splitlines() if row.startswith("Ready card to pick up:")]
        again = self.tick(BABYSIT)
        step("pickup gate: with pickup on, one line per free-slot candidate wakes the agent once, naming the unit and the card; the same candidates on the next tick are quiet (no cost loop)",
             wakes(loud.stdout) and lines == ["Ready card to pick up: fix-search (card t_41)", "Ready card to pick up: add-shortcut (card t_42)"]
             and self.woke(loud, "faker-kit pickup: 2 ready unit card(s) to pick up") and self.silent(again), self.say(loud))
        report([{"unit": "fix-search", "card": "t_41"}, {"unit": "later-card", "card": "t_43"}])
        added = self.tick(BABYSIT)
        picked = [r for r in added.stdout.splitlines() if r.startswith("Ready card to pick up:")]
        step("pickup gate: a newly listed card prints one line for that card only", picked == ["Ready card to pick up: later-card (card t_43)"], self.say(added))
        self.program("pickup-status", "", 1, "boom")
        broken = self.tick(BABYSIT)
        step("pickup gate: an unreadable pickup status prints nothing and the gate stays quiet", self.silent(broken), self.say(broken))
        blocked = {"unit": "stuck-unit", "card": "t_44", "blocked": True, "reason": "x"}
        report([])
        self.program("loop-reconcile", json.dumps({**RECONCILE_QUIET, "blocked": [blocked]}))
        told = self.tick(BABYSIT)
        twice = self.tick(BABYSIT)
        step("pickup gate: a unit blocked after its one steer is reported once and then stays quiet",
             wakes(told.stdout) and "unit stuck-unit (card t_44) was blocked" in told.stdout and self.silent(twice), self.say(told))
        self.program("loop-reconcile", json.dumps(RECONCILE_QUIET))
        self.program("pickup-status")
        self.reset()

    def conflicts(self) -> None:
        step = self.run.step
        head = "e" * 40
        row = {"pr": 7, "head": head, "decision": "tell-owner", "action": "tell-owner", "line": f"PR #7 still conflicting after the follow-up, head {head}"}
        steered = {"pr": 8, "head": "f" * 40, "decision": "steer", "action": "steered", "session": "session_01E2Eabc"}
        self.reset()
        self.serve(STATUS)
        self.tick(BABYSIT)
        self.program("session-conflicts", json.dumps({"dry_run": False, "open": [f"8:{'f' * 40}"], "conflicts": [steered]}))
        sent = self.tick(BABYSIT)
        step("conflict gate: a tick that only sent a follow-up wakes no model turn and prints nothing", self.silent(sent) and "session conflicts" in self.calls(), self.say(sent))
        self.program("session-conflicts", json.dumps({"dry_run": False, "open": [f"7:{head}", f"8:{'f' * 40}"], "conflicts": [row, steered]}))
        loud = self.tick(BABYSIT)
        lines = [r for r in loud.stdout.splitlines() if r.startswith("faker-kit session conflicts:")]
        again = self.tick(BABYSIT)
        step("conflict gate: a pull request still conflicting after its follow-up wakes the agent with one line, printed once per head",
             self.woke(loud, f"faker-kit session conflicts: {row['line']}") and lines == [f"faker-kit session conflicts: {row['line']}"] and self.silent(again), self.say(loud))
        self.program("session-conflicts", json.dumps({"dry_run": False, "open": [f"8:{'f' * 40}"], "conflicts": [steered]}))
        self.tick(BABYSIT)
        self.program("session-conflicts", json.dumps({"dry_run": False, "open": [f"7:{head}"], "conflicts": [row]}))
        reopened = self.tick(BABYSIT)
        self.program("session-conflicts", json.dumps({"dry_run": False, "open": [f"7:{head}"], "conflicts": []}))
        unsettled = self.tick(BABYSIT)
        self.program("session-conflicts", json.dumps({"dry_run": False, "open": [f"7:{head}"], "conflicts": [row]}))
        settled = self.tick(BABYSIT)
        step("conflict gate: the line returns only after its pull request left the open set; a tick where GitHub had not computed it yet does not print it again",
             wakes(reopened.stdout) and self.silent(unsettled) and self.silent(settled), self.say(reopened))
        self.program("session-conflicts", "", 1, "boom")
        broken = self.tick(BABYSIT)
        broken_again = self.tick(BABYSIT)
        step("conflict gate: an action that cannot run prints one line once and the rest of the tick still ran",
             wakes(broken.stdout) and "faker-kit session conflicts could not run this tick" in broken.stdout and "faker-kit status:" in broken.stdout and self.silent(broken_again),
             self.say(broken))
        self.program("session-conflicts", json.dumps(CONFLICTS_QUIET))
        calls = self.calls()
        step("conflict gate: the action runs after loop reconcile and before the pickup and status reads on every tick",
             calls.index("loop reconcile") < calls.index("session conflicts") < calls.index("pickup status --no-job") < calls.index("status"), " ".join(calls[:6]))
        self.reset()

    def card_events(self) -> None:
        step = self.run.step
        self.serve(STATUS)
        self.tick(BABYSIT)
        refused = {"card": "t_1abb3883", "id": "event-153", "head": "d" * 40, "completion": "refused", "classification": "pending", "detail": "CI acceptance pending on the exact head"}
        unreadable = {"id": "event-154", "reason": "GitHub's answer for #154 is not a pull request of acme/shop"}
        report = {**RECONCILE_QUIET, "entries": {**RECONCILE_QUIET["entries"], "refused": [refused], "unreadable": [unreadable]}}
        self.program("loop-reconcile", json.dumps(report))
        loud = self.tick(BABYSIT)
        lines = [row for row in loud.stdout.splitlines() if row.startswith("faker-kit loop reconcile:")]
        again = self.tick(BABYSIT)
        step("card events: a card Kanban refused and a GitHub read that failed wake the agent with one line each",
             self.woke(loud, "faker-kit loop reconcile: card t_1abb3883 was refused (pending: CI acceptance pending on the exact head); "
                             "its claim is released and the kit tries again only after a growing hold or when the head moves")
             and len(lines) == 2 and "loop reconcile" in self.calls(), self.say(loud))
        step("card events: the same refusal and unreadable read on the next tick print nothing and the gate stays quiet", self.silent(again), self.say(again))
        self.program("loop-reconcile", json.dumps({**RECONCILE_QUIET, "partial": True, "unreached": ["t_9"]}))
        told_before = self.state_of("settle")
        half = self.tick(BABYSIT)
        step("card events: a partial reconcile does not forget what it already told, so the cards it did not reach are not announced again when a later pass reaches them",
             self.silent(half) and self.state_of("settle") == told_before and len(told_before) == 2, str(self.state_of("settle")))
        self.program("loop-reconcile", json.dumps(RECONCILE_QUIET))
        kept = self.snapshot(BABYSIT)
        cleared = self.tick(BABYSIT)
        step("card events: a tick with nothing to settle prints nothing, adds no model turn, and writes no gate state", self.silent(cleared) and self.snapshot(BABYSIT) == kept, self.say(cleared))
        self.program("loop-reconcile", "", 1, "boom")
        broken = self.tick(BABYSIT)
        broken_again = self.tick(BABYSIT)
        step("card events: a reconcile that cannot run prints one line once and leaves the rest of the gate working",
             wakes(broken.stdout) and "could not settle the unit cards" in broken.stdout and self.silent(broken_again), self.say(broken))
        self.program("loop-reconcile", json.dumps(RECONCILE_QUIET))
        self.reset()
        pending = {"card": "t_1487ed28", "id": "post-update-claude-path", "head": "e" * 40, "completion": "pending", "classification": "pending", "detail": "CI acceptance pending on the exact head"}
        held_back = json.dumps({**RECONCILE_QUIET, "entries": {**RECONCILE_QUIET["entries"], "completion_pending": [pending]}})
        self.serve(STATUS)
        self.tick(BABYSIT)
        self.program("loop-reconcile", held_back)
        alone = self.tick(BABYSIT)
        kept_alone = self.state_of("pending")
        self.serve([*STATUS[:1], STATUS[1].replace("conflicts", "mergeable")])
        self.program("loop-reconcile", held_back)
        loud = self.tick(BABYSIT)
        notices = [row for row in loud.stdout.splitlines() if row.startswith("Notice, not a blocker:")]
        self.serve(STATUS)
        self.program("loop-reconcile", held_back)
        again = self.tick(BABYSIT)
        step("card events: a merged card whose completion Kanban holds back is a notice, not a blocker: it wakes nothing alone, prints once with the next wake as a notice naming the card and "
             "the classification, and is not printed again",
             self.silent(alone) and kept_alone == [] and self.woke(loud, CHANGED) and len(notices) == 1 and "t_1487ed28" in notices[0] and "pending: CI acceptance pending" in notices[0]
             and "its slot is free" in notices[0] and not [row for row in again.stdout.splitlines() if row.startswith("Notice, not a blocker:")]
             and self.state_of("pending") == [f"pending:t_1487ed28:{'e' * 40}"],
             self.say(loud))
        self.program("loop-reconcile", json.dumps(RECONCILE_QUIET))
        self.tick(BABYSIT)
        self.reset()

    def state_of(self, kind: str) -> list[str]:
        path = self.states[BABYSIT].parent / f"{BABYSIT}-{kind}.json"
        return json.loads(path.read_text()) if path.is_file() else []

    def quiet_without_pulls(self) -> None:
        step = self.run.step
        self.reset()
        self.serve([NO_PULLS])
        lists = self.hermes_lists()
        opening = self.tick(BABYSIT)
        step("with no pull request open, no issue, and no notice the first tick is quiet, with no first-run wake, and records the empty status so the next pull request counts as a change",
             self.silent(opening) and self.state(BABYSIT)["fingerprint"] == fingerprint_of([NO_PULLS]) and self.calls()[-4:] == ["status", "news", "loop check", "loop show"]
             and self.hermes_lists() == lists, self.say(opening))
        self.set_woke_at(BABYSIT, time.time() - 7 * 60 * 60)
        kept = self.snapshot(BABYSIT)
        late = self.tick(BABYSIT)
        step("seven hours later with still no pull request open the gate stays quiet and writes nothing: no heartbeat", self.silent(late) and self.snapshot(BABYSIT) == kept, self.say(late))
        self.serve(STATUS)
        appeared = self.tick(BABYSIT)
        step("a pull request that opens after the quiet stretch wakes the agent as a change",
             self.woke(appeared, CHANGED) and self.state(BABYSIT)["fingerprint"] == fingerprint_of(STATUS), self.say(appeared))
        self.serve([NO_PULLS])
        lists = self.hermes_lists()
        closed = self.tick(BABYSIT)
        step("when the last pull request closes the gate wakes the agent once with the last-pull-request reason, records the empty status, and stores the wake time",
             self.woke(closed, LAST_GONE) and self.state(BABYSIT)["fingerprint"] == fingerprint_of([NO_PULLS]) and abs(self.state(BABYSIT)["woke_at"] - time.time()) < MARGIN
             and self.state(BABYSIT)["retries"] == 0, self.say(closed))
        kept = self.snapshot(BABYSIT)
        lists = self.hermes_lists()
        again = self.tick(BABYSIT)
        step("the very next tick with still no pull request open is quiet, writes nothing, and never reads the job's last run",
             self.silent(again) and self.snapshot(BABYSIT) == kept and self.hermes_lists() == lists, self.say(again))
        self.set_woke_at(BABYSIT, time.time() - 7 * 60 * 60)
        kept = self.snapshot(BABYSIT)
        late_again = self.tick(BABYSIT)
        step("seven hours after the last-pull-request wake with still none open the gate stays quiet and writes nothing",
             self.silent(late_again) and self.snapshot(BABYSIT) == kept, self.say(late_again))
        self.serve([STATUS[0], STATUS[1]])
        self.tick(BABYSIT)
        self.serve([STATUS[0]])
        remaining = self.tick(BABYSIT)
        step("with two pull requests open and one merged, one remains, the gate wakes with the ordinary changed reason",
             self.woke(remaining, CHANGED) and self.state(BABYSIT)["fingerprint"] == fingerprint_of([STATUS[0]]), self.say(remaining))
        self.serve([NO_PULLS])
        self.tick(BABYSIT)
        self.set_last_run("error: the previous woken turn failed", time.time())
        failed = self.tick(BABYSIT)
        step("a failed earlier run does not wake the gate while no pull request is open", self.silent(failed), self.say(failed))
        self.set_last_run(None)

        self.serve([NO_PULLS], issues_json([LABELED]))
        listed = self.tick(BABYSIT)
        step("a listed issue wakes the agent with it even with no pull request open, and stores the wake time",
             self.woke(listed, ISSUES_REASON) and listed.stdout.splitlines()[2] == issues_json([LABELED])
             and abs(self.state(BABYSIT)["woke_at"] - time.time()) < MARGIN, self.say(listed))
        self.serve([NO_PULLS, NOTICE])
        notice = self.tick(BABYSIT)
        step("a faker-kit notice wakes the agent even with no pull request open", self.woke(notice, NOTICE_REASON) and NOTICE in notice.stdout.splitlines(), self.say(notice))
        self.serve([NO_PULLS])
        settled = self.tick(BABYSIT)
        step("the tick after the notice is gone is quiet again", self.silent(settled), self.say(settled))

    def stuck_units(self) -> None:
        step = self.run.step
        session = "https://claude.ai/code/session_01E2Eabc"
        line = "Unit without a pull request past its timebox: csv-export"

        def unit(name: str = "csv-export", **fields: object) -> dict:
            return {"unit": name, "card": "9", "state": "in-progress", "prs": [], "part": None, "outcome": "merged", "closed": False,
                    "added": time.time() - 4 * 60 * 60, "sessions": [session], "steered": False, **fields}

        def tick_with(rows: list[dict], status: list[str]) -> subprocess.CompletedProcess[str]:
            self.serve(status)
            self.program("loop", json.dumps({"units": rows}))
            return self.tick(BABYSIT)

        self.reset()
        self.set_last_run(None)
        opening = tick_with([], [NO_PULLS])
        step("a tick with no pull request open and no unit past its timebox stays quiet and stores an empty stuck list", self.silent(opening) and self.state(BABYSIT)["stuck"] == [], self.say(opening))
        woke = tick_with([unit()], [NO_PULLS])
        step("a unit card with a session, no pull request, added four hours ago wakes the agent once with one line naming the unit, and the gate records the unit and session",
             self.woke(woke, STUCK_REASON) and woke.stdout.splitlines()[-1] == line and woke.stdout.count("Unit without a pull request") == 1
             and self.state(BABYSIT)["stuck"] == [f"csv-export|{session}"] and self.calls()[-1] == "loop show", self.say(woke))
        kept = self.snapshot(BABYSIT)
        again = tick_with([unit()], [NO_PULLS])
        step("the same tick again prints nothing and writes nothing", self.silent(again) and self.snapshot(BABYSIT) == kept, self.say(again))
        for name, rows in (("a unit already steered", [unit(steered=True)]), ("a unit under three hours", [unit(added=time.time() - 2 * 60 * 60)]),
                           ("a unit with a pull request", [unit(prs=[7])]), ("a closed unit", [unit(closed=True)]), ("a folded unit", [unit(state="folded", closed=True)]),
                           ("a unit with no session", [unit(sessions=[])]), ("a unit with no recorded start", [unit(added=None)])):
            self.reset()
            quiet = tick_with(rows, [NO_PULLS])
            step(f"{name} wakes nothing and prints no line", self.silent(quiet) and "stuck" in self.state(BABYSIT) and self.state(BABYSIT)["stuck"] == [], self.say(quiet))
        for name, out, code in (("an unreadable board", "", 1), ("a board report that is not JSON", "not json", 0), ("a report with no unit list", json.dumps({"parts": []}), 0)):
            self.reset()
            self.serve([NO_PULLS])
            self.program("loop", out, code)
            broken = self.tick(BABYSIT)
            step(f"{name} changes nothing and does not wake", self.silent(broken) and self.state(BABYSIT)["stuck"] == [], self.say(broken))
        self.reset()
        first = tick_with([unit()], STATUS)
        step("with pull requests open the first tick keeps its ordinary reason and the line follows the usual lines once",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[-1] == line and first.stdout.count("Unit without a pull request") == 1, self.say(first))
        self.set_woke_at(BABYSIT, time.time() - 7 * 60 * 60)
        beat = tick_with([unit()], STATUS)
        step("the heartbeat wake six hours later does not repeat the line", self.woke(beat, HEARTBEAT_REASON) and "Unit without a pull request" not in beat.stdout
             and self.state(BABYSIT)["stuck"] == [f"csv-export|{session}"], self.say(beat))
        later = tick_with([unit(), unit("login-fix", sessions=["https://claude.ai/code/session_01E2Edef"])], STATUS)
        step("a second unit past its timebox on a quiet tick with pull requests open wakes with only its own line",
             self.woke(later, STUCK_REASON) and later.stdout.splitlines()[-1] == "Unit without a pull request past its timebox: login-fix"
             and later.stdout.count("Unit without a pull request") == 1, self.say(later))
        retired = tick_with([], STATUS)
        step("units that leave the board wake nothing and the recorded pairs stay, so a repeat never prints", self.silent(retired) and len(self.state(BABYSIT)["stuck"]) == 2, self.say(retired))

    def failed_runs(self, lines: list[str]) -> None:
        step = self.run.step
        self.reset()
        self.serve(lines)
        self.tick(BABYSIT)

        def probe(outcome: str | None, run_at: float, reason: str | None) -> bool:
            self.set_woke_at(BABYSIT, time.time() - 600, retries=0)
            self.set_last_run(outcome, run_at)
            done = self.tick(BABYSIT)
            return self.silent(done) if reason is None else self.woke(done, reason)

        recent = time.time() - 300
        retried = {outcome: probe(outcome, recent, RUN_FAILED) for outcome in ("error: model refused the turn", "blocked_config: no provider", "interrupted: the gateway restarted")}
        step("a previous run after the last wake that errored, was blocked, or was interrupted wakes the agent even when the status is unchanged", all(retried.values()), json.dumps(retried))
        finished = ("ok", "finished; delivery is still in progress", "ran, but the result was not delivered (slack: channel not found). Check the target.", "cancelled: ?")
        left = {outcome: probe(outcome, recent, None) for outcome in finished}
        left["no last run line"] = probe(None, recent, None)
        left["an error from before the last wake"] = probe("error: an old failure", time.time() - 3600, None)
        step("a previous run that finished ok, finished with only its delivery pending or failed, was cancelled, never ran, or errored before the last wake does not wake it",
             all(left.values()), json.dumps(left))

        self.set_woke_at(BABYSIT, time.time() - 600, retries=0)
        self.set_last_run("error: model refused the turn", recent)
        first = self.tick(BABYSIT)
        woke_at = self.state(BABYSIT)["woke_at"]
        second = self.tick(BABYSIT)
        step("the retry wakes the agent once: the wake stores a newer wake time, so the same failed run does not wake the next tick",
             self.woke(first, RUN_FAILED) and woke_at > recent and self.silent(second), self.say(second))
        self.set_last_run("error: the retry failed too", time.time() + 5)
        again = self.tick(BABYSIT)
        step("a retry that fails too is retried again, because each failed run after a wake is a new failure", self.woke(again, RUN_FAILED) and self.state(BABYSIT)["retries"] == 2, self.say(again))
        self.set_last_run("error: the third try failed too", time.time() + 10)
        third = self.tick(BABYSIT)
        step("each wake for a failed run adds one to retries in the state file, up to three", self.woke(third, RUN_FAILED) and self.state(BABYSIT)["retries"] == 3, self.say(third))
        self.set_last_run("error: the fourth try failed too", time.time() + 20)
        lists = self.hermes_lists()
        kept = self.snapshot(BABYSIT)
        capped = self.tick(BABYSIT)
        step("with three failed-run wakes in a row a failed last run no longer wakes the agent, nothing is written, and the run list is not even read",
             self.silent(capped) and self.snapshot(BABYSIT) == kept and self.hermes_lists() == lists, self.say(capped))
        self.reset_after_cap(lines)

        flag = self.hermes / "cron-list-fails"
        unreadable: dict[str, Callable[[], object]] = {
            "hermes cron list exits non-zero": lambda: flag.write_text("x"),
            "the job is missing from the list": lambda: self.set_job_name("faker-kit-other"),
            "the last run time is not a time": self.corrupt_last_run,
            "the hermes executable is missing": lambda: [path.rename(path.with_name("hermes.aside")) for path in self.hermes_bins],
        }
        outcome = {}
        for label, arrange in unreadable.items():
            self.reset()
            self.serve(lines)
            self.tick(BABYSIT)
            self.set_woke_at(BABYSIT, time.time() - 600, retries=2)
            kept = self.snapshot(BABYSIT)
            arrange()
            done = self.tick(BABYSIT)
            outcome[label] = self.silent(done) and done.stderr == "" and self.snapshot(BABYSIT) == kept
            flag.unlink(missing_ok=True)
            for path in self.hermes_bins:
                if path.with_name("hermes.aside").exists():
                    path.with_name("hermes.aside").rename(path)
            self.set_job_name(BABYSIT)
            self.set_last_run(None)
        step("when the last run cannot be read (listing fails, job absent, unreadable time, no hermes) the gate stays quiet, writes nothing, and prints nothing on stderr",
             all(outcome.values()), json.dumps(outcome))
        self.reset()

    def reset_after_cap(self, lines: list[str]) -> None:
        step = self.run.step
        wakers: dict[str, tuple[Callable[[], None], str]] = {
            "the heartbeat falling due": (lambda: self.set_woke_at(BABYSIT, time.time() - HEARTBEAT - MARGIN), HEARTBEAT_REASON),
            "a changed status": (lambda: self.serve([*lines[:1], lines[1].replace("conflicts", "mergeable")]), CHANGED),
            "a faker-kit notice": (lambda: self.serve([*lines, NOTICE]), NOTICE_REASON),
            "a listed issue": (lambda: self.serve(lines, issues_json([LABELED])), ISSUES_REASON),
        }
        outcome = {}
        for label, (arrange, reason) in wakers.items():
            self.serve(lines)
            self.tick(BABYSIT)
            self.set_woke_at(BABYSIT, time.time() - 600, retries=3)
            self.set_last_run("error: the model refused the turn", time.time() - 300)
            arrange()
            done = self.tick(BABYSIT)
            reset = self.woke(done, reason) and self.state(BABYSIT)["retries"] == 0
            self.serve(lines)
            self.tick(BABYSIT)
            self.set_woke_at(BABYSIT, time.time() - 600)
            self.set_last_run("error: the model refused the turn", time.time() - 300)
            retried = self.tick(BABYSIT)
            outcome[label] = reset and self.woke(retried, RUN_FAILED) and self.state(BABYSIT)["retries"] == 1
            self.set_last_run(None)
        step("at the cap a due heartbeat, a changed status, a notice, or a listed issue still wakes the agent and resets retries to 0, after which failed runs wake it again",
             all(outcome.values()), json.dumps(outcome))
        self.serve(lines)
        self.tick(BABYSIT)

    def set_job_name(self, name: str) -> None:
        jobs = json.loads(self.jobs_file.read_text())
        for job in jobs:
            if job["name"] in (BABYSIT, "faker-kit-other"):
                job["name"] = name
        self.jobs_file.write_text(json.dumps(jobs, sort_keys=True))

    def corrupt_last_run(self) -> None:
        jobs = json.loads(self.jobs_file.read_text())
        for job in jobs:
            if job["name"] == BABYSIT:
                job["last_run"] = "yesterday-ish  ok"
        self.jobs_file.write_text(json.dumps(jobs, sort_keys=True))

    def damaged_state(self) -> None:
        step = self.run.step
        self.reset()
        self.serve(STATUS)
        self.tick(BABYSIT)
        damaged = {"not json": "{not json", "a list": "[]", "no fingerprint": json.dumps({"woke_at": time.time()}), "a text time": json.dumps({"fingerprint": "x", "woke_at": "now"}),
                   "a boolean time": json.dumps({"fingerprint": "x", "woke_at": True}), "a time that is not finite": '{"fingerprint": "x", "woke_at": NaN}'}
        rewritten = {}
        for label, text in damaged.items():
            self.states[BABYSIT].write_text(text)
            done = self.tick(BABYSIT)
            rewritten[label] = self.woke(done, NOT_RECORDED) and bool(FINGERPRINT.match(str(self.state(BABYSIT).get("fingerprint"))))
        step("a state file that is unreadable or malformed counts as no earlier tick: the agent wakes and the file is written whole again", all(rewritten.values()), json.dumps(rewritten))

        self.reset()
        self.serve(STATUS)
        self.tick(BABYSIT)
        stored = self.state(BABYSIT)
        self.states[BABYSIT].write_text(json.dumps({"fingerprint": stored["fingerprint"], "woke_at": time.time() - 600}))
        self.set_last_run(None)
        kept = self.snapshot(BABYSIT)
        legacy = self.tick(BABYSIT)
        unwritten = self.snapshot(BABYSIT) == kept
        self.set_last_run("error: the model refused the turn", time.time() - 300)
        failed = self.tick(BABYSIT)
        step("a state file written before retries existed reads as 0 retries: it stays quiet and unwritten while nothing changed, wakes for a failed run, and then holds retries 1",
             self.silent(legacy) and unwritten and self.woke(failed, RUN_FAILED) and self.state(BABYSIT)["retries"] == 1, self.say(failed))
        self.set_last_run(None)
        invalid = {"a text count": "three", "a boolean count": True, "a negative count": -2, "a fractional count": 2.5, "a null count": None, "a list count": [3]}
        counted = {}
        for label, value in invalid.items():
            self.states[BABYSIT].write_text(json.dumps({"fingerprint": stored["fingerprint"], "woke_at": time.time() - 600, "retries": value}))
            self.set_last_run("error: the model refused the turn", time.time() - 300)
            done = self.tick(BABYSIT)
            counted[label] = self.woke(done, RUN_FAILED) and self.state(BABYSIT)["retries"] == 1
            self.set_last_run(None)
        step("a retries value that is not a count of at least one reads as 0 and does not stop the gate", all(counted.values()), json.dumps(counted))

        other = self.hermes / "profiles" / "other-bot"
        other.mkdir(exist_ok=True)
        before = self.snapshot(BABYSIT)
        elsewhere = self.tick(BABYSIT, other)
        step("the state belongs to the profile Hermes runs the gate in: another profile gets its own file and this profile's file is untouched",
             self.woke(elsewhere, NOT_RECORDED) and (self.gates / "other-bot" / f"{BABYSIT}.json").is_file() and self.snapshot(BABYSIT) == before, self.say(elsewhere))

    def failing_commands(self) -> None:
        step = self.run.step
        arranged: dict[str, tuple[Callable[[], None], str]] = {
            "faker-kit status exits non-zero": (lambda: self.program("status", "\n".join(STATUS), code=3, err="gh: Internal Server Error (HTTP 500)"), "faker-kit status exited 3"),
            "faker-kit status prints nothing": (lambda: self.program("status", ""), "faker-kit status printed nothing"),
            "faker-kit issues exits non-zero": (lambda: self.program("issues", issues_json([LABELED]), code=1, err="faker-kit: refused: no collaborator"), "faker-kit issues exited 1"),
            "faker-kit issues prints no JSON": (lambda: self.program("issues", "no issues here"), "faker-kit issues printed no JSON"),
            "faker-kit issues prints a list": (lambda: self.program("issues", "[]"), "faker-kit issues printed an unexpected report shape"),
            "faker-kit issues prints issues that are not a list": (lambda: self.program("issues", json.dumps({"issues": "none"})), "faker-kit issues printed an unexpected report shape"),
        }
        outcome = {}
        for label, (arrange, message) in arranged.items():
            self.reset()
            self.serve(STATUS)
            self.tick(BABYSIT)
            self.set_woke_at(BABYSIT, time.time() - HEARTBEAT - MARGIN)
            kept = self.snapshot(BABYSIT)
            self.serve(STATUS)
            arrange()
            stale = self.tick(BABYSIT)
            outcome[f"{label}, with the heartbeat due"] = self.failed(stale, message) and self.snapshot(BABYSIT) == kept
            self.reset()
            fresh = self.tick(BABYSIT)
            outcome[f"{label}, with no state"] = self.failed(fresh, message) and not self.gates.exists()
        step("when faker-kit status or issues fails or prints an unexpected shape the gate exits non-zero with a short message and nothing on stdout, and writes no state, heartbeat due or not",
             all(outcome.values()), json.dumps(outcome))
        aside = self.wrapper.with_name("faker-kit.aside")
        self.reset()
        self.wrapper.rename(aside)
        missing = self.tick(BABYSIT)
        aside.rename(self.wrapper)
        step("a wrapper that cannot be run fails the tick the same way, naming only the error's type, and stores nothing",
             self.failed(missing, "faker-kit issues did not run: FileNotFoundError") and not self.states[BABYSIT].exists(), self.say(missing))
        self.serve(STATUS)
        self.reset()

    def loop(self) -> None:
        step = self.run.step
        waiting = part(2, "waiting", waits_on="shop#7")
        provider = part(1, "merged", prs=[7])
        news = part(3, "blocked", reported=None)
        due = [waiting]

        def serve_loop(parts: list[dict], to_report: list[dict] | None = None) -> subprocess.CompletedProcess[str]:
            self.program("loop", loop_json(parts, to_report))
            return self.tick(LOOP)

        (self.fake / "calls.log").unlink(missing_ok=True)
        quiet = serve_loop([])
        step("the loop gate runs one faker-kit loop --all and stays quiet when the chief holds no part", self.silent(quiet) and self.calls() == ["loop --all"], self.say(quiet))
        idle = serve_loop([part(4, "in-progress"), part(5, "handed-out", prs=[9]), part(6, "ready-to-merge", reported="ready-to-merge")])
        step("parts that are only in progress, handed out, or already reported keep it quiet", self.silent(idle), self.say(idle))
        pending = serve_loop([waiting, part(1, "in-progress", prs=[7])])
        elsewhere = serve_loop([waiting, part(8, "merged", prs=[7], project="other")])
        different = serve_loop([waiting, part(9, "merged", prs=[8])])
        step("a waiting part stays quiet while its provider's pull request is not in a merged part of the same project",
             self.silent(pending) and self.silent(elsewhere) and self.silent(different), "provider open, provider in another project, provider on another pull request")
        woke = serve_loop([provider, waiting])
        step("a waiting part whose provider part is merged wakes the chief with that part as its Script Output",
             self.woke(woke, LOOP_REASON) and woke.stdout.splitlines()[1:] == ["faker-kit loop:", json.dumps({"due_waiting": due, "to_report": []}, sort_keys=True)], self.say(woke))
        reported = serve_loop([part(3, "blocked")], [news])
        step("a part listed under to_report wakes the chief with it",
             self.woke(reported, LOOP_REASON) and json.loads(reported.stdout.splitlines()[2]) == {"due_waiting": [], "to_report": [news]}, self.say(reported))
        ready = part(14, "ready-to-merge", prs=[21])
        repeated = serve_loop([provider, waiting, waiting], [news, news, ready, part(15, "ready-to-merge", prs=[21])])
        step("a part listed twice, or a second part naming a pull request another part already reports, is reported once in the chief's wake",
             self.woke(repeated, LOOP_REASON) and json.loads(repeated.stdout.splitlines()[2]) == {"due_waiting": due, "to_report": [news, ready]}, self.say(repeated))
        both = serve_loop([provider, waiting, news], [news])
        step("a due part and a reportable part wake the chief once with both",
             self.woke(both, LOOP_REASON) and json.loads(both.stdout.splitlines()[2]) == {"due_waiting": due, "to_report": [news]}, self.say(both))
        bad = {"faker-kit loop --all exits non-zero": (lambda: self.program("loop", loop_json([]), code=1, err="faker-kit: refused: the loop store cannot be read"), "faker-kit loop --all exited 1"),
               "faker-kit loop --all prints no JSON": (lambda: self.program("loop", "nothing"), "faker-kit loop --all printed no JSON"),
               "faker-kit loop --all prints a list": (lambda: self.program("loop", "[]"), SHAPE),
               "faker-kit loop --all prints no to_report": (lambda: self.program("loop", json.dumps({"open": []})), SHAPE),
               "faker-kit loop --all prints a part without its pull requests": (lambda: self.program("loop", json.dumps({"open": [NO_PRS_PART], "to_report": []})), SHAPE)}
        outcome = {}
        for label, (arrange, message) in bad.items():
            arrange()
            outcome[label] = self.failed(self.tick(LOOP), message)
        step("when faker-kit loop --all fails or prints an unexpected shape the loop gate exits non-zero with a short message and nothing on stdout, so the chief runs the pass itself",
             all(outcome.values()), json.dumps(outcome))
        step("the loop gate keeps no state of its own", not self.gates.exists() or not any(self.gates.glob("**/faker-kit-loop*")), "no loop gate file under the gates folder")

    def real_loop(self, chief: str, env: dict[str, str]) -> None:
        step = self.run.step

        def kit(*argv: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([str(self.wrapper), *argv], env={**env, "HERMES_HOME": str(self.chief_profile)}, capture_output=True, text=True, timeout=120, check=False)

        quiet = self.tick(LOOP)
        handed = kit("handoff", "--project", "shop", "--part", "Add search to the catalog", "--decisions", "none: fixture")
        working = self.tick(LOOP)
        step("the loop gate runs the installed faker-kit loop --all as the chief: an empty loop and a freshly handed-out part are both quiet",
             handed.returncode == 0 and self.silent(quiet) and self.silent(working), handed.stderr.strip() or self.say(working))
        kit("loop", "set", "1", "--state", "ready-to-merge", "--pr", "12")
        news = self.tick(LOOP)
        step("a part that becomes ready to merge wakes the chief with the real part in its output",
             self.woke(news, LOOP_REASON) and json.loads(news.stdout.splitlines()[2])["to_report"][0]["prs"] == [12], self.say(news))
        kit("loop", "set", "1", "--reported")
        told = self.tick(LOOP)
        step("once the chief marks it reported the gate is quiet again", self.silent(told), self.say(told))
        kit("loop", "set", "1", "--state", "merged")
        kit("loop", "set", "1", "--reported")
        consumer = kit("handoff", "--project", "shop", "--part", "Wire search into the app", "--decisions", "none: fixture")
        kit("loop", "set", "2", "--state", "waiting", "--waits-on", "shop#12")
        due = self.tick(LOOP)
        step("a waiting part whose provider's pull request is in a merged part is due: the real loop wakes the chief with it",
             consumer.returncode == 0 and self.woke(due, LOOP_REASON) and [p["id"] for p in json.loads(due.stdout.splitlines()[2])["due_waiting"]] == [2], self.say(due))
        kit("loop", "set", "2", "--state", "handed-out")
        handed_over = self.tick(LOOP)
        step("after the chief hands the part over the gate is quiet", self.silent(handed_over), self.say(handed_over))
        store = Path(env["FAKER_KIT_HOME"]) / "loop" / f"{chief}.json"
        shutil.copyfile(store, self.fake / "loop-store.json")
        store.write_text("{not json")
        broken = self.tick(LOOP)
        shutil.copyfile(self.fake / "loop-store.json", store)
        step("a loop store the command cannot read fails the tick with a short message instead of staying quiet", self.failed(broken, "faker-kit loop --all exited 1"), self.say(broken))


    def news_state(self) -> dict:
        path = self.gates.parent / "chief-news" / f"{self.bot}.json"
        return json.loads(path.read_text()) if path.is_file() else {}

    def deliveries(self) -> list[dict]:
        log = self.hermes / "deliveries.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []

    def news_runs(self) -> int:
        log = self.hermes / "hermes-calls.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
        return sum(1 for call in calls if call == ["-p", self.bot, "cron", "run", NEWS_JOB]) - self.runs_before

    def reset_news(self) -> None:
        shutil.rmtree(self.gates.parent / "chief-news", ignore_errors=True)
        (self.hermes / "deliveries.log").unlink(missing_ok=True)
        (self.hermes / "delivery-unsettled").unlink(missing_ok=True)

    def real_news(self) -> None:
        step, now = self.run.step, self.run.gh_now
        self.reset()
        self.reset_news()
        shown = next(pull for pull in now()["pulls"] if pull["state"] == "open")
        units = self.gates.parent / "briefs" / self.bot / "units.tsv"
        units.parent.mkdir(parents=True, exist_ok=True)
        held = units.read_text() if units.is_file() else None
        cells = ["csv-export", "running", "b", str(shown["number"]), "", "", "csv.md", "sonnet", "medium", "chief", "", "claude"]
        units.write_text("unit\tstate\tbranch\tpr\thead\tsession\tbrief\tmodel\teffort\torigin\ttold_chief\tbackend\n" + "\t".join(cells) + "\n")
        done = self.tick(BABYSIT)
        sent = self.deliveries()
        lines = [line for d in sent for line in d["output"].splitlines()]
        step("the real faker-kit news reads a units.tsv row whose origin is chief, and the real babysit gate delivers its opened note to the chief's Bot Chat with no model turn",
             done.returncode == 0 and len(sent) == 1 and sent[0]["deliver"] == f"bot-chat:{self.chief_name}" and sent[0]["job"] == NEWS_JOB
             and lines[0] == f"Chief news: unit csv-export pull request #{shown['number']} https://github.com/acme/shop/pull/{shown['number']} opened.", json.dumps(sent)[:300])
        if held is None:
            units.unlink()
        else:
            units.write_text(held)
        self.reset_news()
        self.reset()

    def chief_news(self) -> None:
        step = self.run.step
        self.reset()
        self.reset_news()
        self.set_last_run(None)
        self.runs_before += self.news_runs()
        pull_url = "https://github.com/acme/shop/pull/1"
        row = {"unit": "csv", "number": 1, "url": pull_url, "state": "open", "draft": False, "head": "a" * 40}
        old = {"unit": "old", "number": 5, "url": "https://github.com/acme/shop/pull/5", "state": "merged", "draft": False, "head": "e" * 40}
        self.serve(STATUS)
        self.program("news", json.dumps({"tracked": [old]}))
        self.tick(BABYSIT)
        step("a first chief-news run records pull requests that already merged as sent and delivers nothing",
             self.deliveries() == [] and self.news_runs() == 0 and self.news_state().get("sent") == ["5|merged|"], json.dumps(self.news_state()))
        self.program("news", json.dumps({"tracked": [old, row]}))
        self.tick(BABYSIT)
        sent = self.deliveries()
        step("a tracked ready pull request whose status reads ready to merge gives one opened and one needs-the-owner note, in one delivery to the chief's Bot Chat",
             len(sent) == 1 and sent[0]["deliver"] == f"bot-chat:{self.chief_name}" and sent[0]["output"].splitlines()
             == [f"Chief news: unit csv pull request #1 {pull_url} opened.", f"Chief news: unit csv pull request #1 {pull_url} needs the owner."]
             and self.news_state()["pending"] == [], json.dumps(sent)[:400])
        self.tick(BABYSIT)
        step("the same event again delivers nothing and runs no delivery job", len(self.deliveries()) == 1 and self.news_runs() == 1, str(self.news_runs()))
        pushed = [STATUS[0].replace("a" * 40, "d" * 40).replace("ready to merge", "blocked: checks have not all passed (0 failed, 1 pending)"), STATUS[1]]
        self.serve(pushed)
        self.program("news", json.dumps({"tracked": [old, {**row, "head": "d" * 40}]}))
        self.tick(BABYSIT)
        step("a push to the open pull request delivers nothing", len(self.deliveries()) == 1, str(len(self.deliveries())))
        self.serve([STATUS[1]])
        self.program("news", json.dumps({"tracked": [old, {**row, "head": "d" * 40, "state": "merged"}]}))
        self.tick(BABYSIT)
        self.tick(BABYSIT)
        sent = self.deliveries()
        step("a merge event delivers exactly one merged note with the unit, number, and link, once, to the chief bot",
             len(sent) == 2 and sent[1]["output"] == f"Chief news: unit csv pull request #1 {pull_url} merged.\n" and sent[1]["deliver"] == f"bot-chat:{self.chief_name}"
             and self.news_runs() == 2, json.dumps(sent[1:])[:300])
        runs = self.news_runs()
        self.program("news", json.dumps({"tracked": [old, {**row, "head": "d" * 40, "state": "merged"}]}))
        self.serve([STATUS[1]])
        self.tick(BABYSIT)
        step("a pull request not tracked in the store, and no news at all, runs no delivery job", self.news_runs() == runs, str(self.news_runs()))
        (self.hermes / "delivery-unsettled").write_text("")
        third = {**row, "number": 3, "url": "https://github.com/acme/shop/pull/3", "unit": "csv-two"}
        self.program("news", json.dumps({"tracked": [third]}))
        failed = self.tick(BABYSIT)
        again = self.tick(BABYSIT)
        step("an unsettled delivery receipt keeps the note pending, the babysit wake still completes and says so in one line once, and the next event retries it",
             failed.returncode == 0 and "1 chief news note(s) not delivered yet: the delivery receipt has not settled; the next event wake retries them" in failed.stdout.splitlines()
             and "faker-kit notice:" not in failed.stdout and again.returncode == 0 and "not delivered yet" not in again.stdout
             and len(self.news_state()["pending"]) == 1 and self.news_runs() == runs + 2, self.say(failed))
        (self.hermes / "delivery-unsettled").unlink()
        self.tick(BABYSIT)
        settled = self.deliveries()
        step("a settled receipt on the next event wake marks the note sent and nothing stays pending",
             settled[-1]["settled"] is True and settled[-1]["output"] == "Chief news: unit csv-two pull request #3 https://github.com/acme/shop/pull/3 opened.\n"
             and self.news_state()["pending"] == [] and "3|opened|" in self.news_state()["sent"], json.dumps(self.news_state()))
        self.reset_news()
        self.reset()


def real_wake_scenario(run: Run, hermes: Path, bot: str, chief: str, env: dict[str, str], work: Path) -> None:
    gates = Gates(run, hermes, bot, chief, env, work)
    gates.real_command()
    gates.real_news()
    gates.real_loop(chief, env)


def fake_wake_scenario(run: Run, hermes: Path, bot: str, chief: str, env: dict[str, str], work: Path) -> None:
    gates = Gates(run, hermes, bot, chief, env, work)
    with gates.fake_wrapper():
        gates.babysit()
        gates.card_check()
        gates.ready_gate()
        gates.conflicts()
        gates.pickup()
        gates.stuck_units()
        gates.summaries()
        gates.chief_news()
        gates.loop()
