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
HEARTBEAT = 6 * 60 * 60
MARGIN = 120
NOT_RECORDED = "no earlier tick of this gate is recorded"
CHANGED = "the status changed"
NOTICE_REASON = "a faker-kit notice printed"
HEARTBEAT_REASON = "six hours passed since this gate last woke the agent while a pull request is open"
LAST_GONE = "the last open pull request merged or closed"
RUN_FAILED = "the job's previous run after the last wake did not finish ok"
SHAPE = "faker-kit loop --all printed an unexpected report shape"
NO_PRS_PART = {"id": 1, "project": "shop", "state": "waiting", "waits_on": None}
LOOP_REASON = "a waiting part is due or a part has news for the owner"
ISSUES_REASON = "faker-kit issues lists 1 issue(s) to handle"
NO_PULLS = "no open pull requests"
NOTICE = "faker-kit notice: something the kit's own code reports"
STATUS = [
    f"#1 | unit csv | by alice | ready | mergeable | updated 2026-11-09T01:00:00Z | head {'a' * 40} | ready to merge",
    f"#2 | by bob | draft | conflicts | updated 2026-11-09T02:00:00Z | head {'b' * 40} | blocked: checks have not all passed (0 failed, 1 pending)",
]
LABELED = {"number": 11, "labeler": "alice", "url": "https://github.com/acme/shop/issues/11", "event": 101}
REAL_LABELED = {"number": 21, "labeler": "alice", "url": "https://github.com/acme/shop/issues/21", "event": 301}
FINGERPRINT = re.compile(r"\A[0-9a-f]{64}\Z")
SHORT_STDERR = 500


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


def part(number: int, state: str, **fields: object) -> dict:
    return {"id": number, "project": "shop", "bot": "meo-beo", "part": f"part {number}", "state": state, "prs": [], "waits_on": None,
            "added": 1.0, "updated": 1.0, "reported": None, **fields}


def loop_json(parts: list[dict], to_report: list[dict] | None = None) -> str:
    return json.dumps({"open": parts, "to_report": to_report or []}, sort_keys=True)


class Gates:
    def __init__(self, run: Run, hermes: Path, bot: str, chief: str, env: dict[str, str], work: Path) -> None:
        self.run, self.bot, self.profile, self.hermes = run, bot, hermes / "profiles" / bot, hermes
        self.chief_profile = hermes / "profiles" / chief
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

    def calls(self) -> list[str]:
        log = self.fake / "calls.log"
        return log.read_text().splitlines() if log.is_file() else []

    @contextmanager
    def fake_wrapper(self) -> Iterator[None]:
        original, mode = self.wrapper.read_bytes(), self.wrapper.stat().st_mode
        (self.fake / "calls.log").unlink(missing_ok=True)
        self.wrapper.write_text("\n".join([
            "#!/bin/sh", f"d={self.fake}", 'printf "%s\\n" "$*" >> "$d/calls.log"',
            '[ -f "$d/$1.err" ] && cat "$d/$1.err" >&2', '[ -f "$d/$1.out" ] && cat "$d/$1.out"',
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
        step("a comment or review on an open pull request moves only its updated time in the real status, which wakes the gate and shows the agent the new time",
             self.woke(commented, CHANGED) and "updated 2026-11-10T09:30:00Z" in commented.stdout, self.say(commented))
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

    def babysit(self) -> None:
        step = self.run.step
        self.reset()
        self.set_last_run(None)
        self.serve(STATUS)
        first = self.tick(BABYSIT)
        stored = self.state(BABYSIT) if self.states[BABYSIT].is_file() else {}
        folder = self.states[BABYSIT].parent
        step("the babysit gate's first tick with a pull request open wakes the agent with the issues JSON and the status lines as its Script Output, after one faker-kit issues and one status",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[1:] == ["faker-kit issues:", issues_json([]), "faker-kit status:", *STATUS]
             and self.calls() == ["issues", "status"], self.say(first))
        step("it stores the status fingerprint and the wake time as an owner-only file, with no temp file left beside it",
             set(stored) == {"fingerprint", "woke_at", "retries"} and stored["retries"] == 0 and stored["fingerprint"] == fingerprint_of(STATUS) and bool(FINGERPRINT.match(stored["fingerprint"]))
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
        touched = self.tick(BABYSIT)
        step("a comment-only change, which moves a pull request's updated time and nothing else, wakes the agent with the new time and stores the new fingerprint",
             self.woke(touched, CHANGED) and touched.stdout.splitlines()[4:] == commented and self.state(BABYSIT)["fingerprint"] == fingerprint_of(commented), self.say(touched))

        changed = [commented[0], commented[1].replace("pending", "failed")]
        self.serve(changed)
        woken = self.tick(BABYSIT)
        step("a changed status wakes the agent with the new lines and stores the new fingerprint",
             self.woke(woken, CHANGED) and woken.stdout.splitlines()[4:] == changed and self.state(BABYSIT)["fingerprint"] == fingerprint_of(changed), self.say(woken))

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
        self.failed_runs(changed)
        self.damaged_state()
        self.failing_commands()

    def quiet_without_pulls(self) -> None:
        step = self.run.step
        self.reset()
        self.serve([NO_PULLS])
        lists = self.hermes_lists()
        opening = self.tick(BABYSIT)
        step("with no pull request open, no issue, and no notice the first tick is quiet, with no first-run wake, and records the empty status so the next pull request counts as a change",
             self.silent(opening) and self.state(BABYSIT)["fingerprint"] == fingerprint_of([NO_PULLS]) and self.calls()[-2:] == ["issues", "status"]
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



def wake_scenario(run: Run, hermes: Path, bot: str, chief: str, env: dict[str, str], work: Path) -> None:
    gates = Gates(run, hermes, bot, chief, env, work)
    gates.real_command()
    gates.real_loop(chief, env)
    with gates.fake_wrapper():
        gates.babysit()
        gates.loop()
