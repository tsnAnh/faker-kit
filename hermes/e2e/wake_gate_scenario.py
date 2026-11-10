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
from pathlib import Path
from typing import Protocol

from gate_scenario import QUIET, wakes

BABYSIT = "faker-kit-babysit"
ISSUES = "faker-kit-issues"
HEARTBEAT = 6 * 60 * 60
MARGIN = 120
NOT_RECORDED = "no earlier tick of this gate is recorded"
CHANGED = "the status changed"
NOTICE_REASON = "a faker-kit notice printed"
HEARTBEAT_REASON = "six hours passed since this gate last woke the agent"
NOTICE = "faker-kit notice: something the kit's own code reports"
STATUS = [
    f"#1 | unit csv | by alice | ready | mergeable | head {'a' * 40} | ready to merge",
    f"#2 | by bob | draft | conflicts | head {'b' * 40} | blocked: checks have not all passed (0 failed, 1 pending)",
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


class Gates:
    def __init__(self, run: Run, hermes: Path, bot: str, env: dict[str, str], work: Path) -> None:
        self.run, self.bot, self.profile, self.hermes = run, bot, hermes / "profiles" / bot, hermes
        self.wrapper = hermes / "bin" / "faker-kit"
        self.gates = Path(env["FAKER_KIT_HOME"]) / "gates"
        self.scripts = {BABYSIT: self.profile / "scripts" / "faker-kit-babysit-gate.py", ISSUES: self.profile / "scripts" / "faker-kit-issues-gate.py"}
        self.states = {job: self.gates / bot / f"{job}.json" for job in self.scripts}
        self.fake = work / "wake-fake"
        self.fake.mkdir()

    def tick(self, job: str, home: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {"PATH": "/usr/bin:/bin", "HERMES_HOME": str(home or self.profile)}
        return subprocess.run([sys.executable, str(self.scripts[job])], env=env, cwd=self.profile.parent, capture_output=True, text=True, timeout=120, check=False)

    def snapshot(self, job: str) -> tuple[bytes, int] | None:
        path = self.states[job]
        return (path.read_bytes(), path.stat().st_mtime_ns) if path.is_file() else None

    def state(self, job: str) -> dict:
        return json.loads(self.states[job].read_text())

    def set_woke_at(self, job: str, woke_at: float) -> None:
        self.states[job].write_text(json.dumps({**self.state(job), "woke_at": woke_at}))

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
        return log.read_text().split() if log.is_file() else []

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
        first = self.tick(BABYSIT)
        stored = self.state(BABYSIT) if self.states[BABYSIT].is_file() else {}
        step("the babysit gate runs the installed faker-kit status: its first tick wakes the agent with the real pull request lines and records their fingerprint",
             self.woke(first, NOT_RECORDED) and " | head " in first.stdout and first.stdout.splitlines()[2].startswith("#") and bool(FINGERPRINT.match(str(stored.get("fingerprint")))),
             self.say(first))
        again = self.tick(BABYSIT)
        step("the real status prints the same lines on the next tick, so the babysit gate skips it", self.silent(again), self.say(again))
        shown = next(pull for pull in now()["pulls"] if pull["state"] == "open")

        def mark(mergeable: bool | None) -> Callable[[dict], object]:
            return lambda state: next(pull for pull in state["pulls"] if pull["number"] == shown["number"]).update(mergeable=mergeable)

        update(mark(False))
        moved = self.tick(BABYSIT)
        update(mark(shown["mergeable"]))
        step("a pull request that starts to conflict changes the real status, which wakes the babysit gate", self.woke(moved, CHANGED) and "conflicts" in moved.stdout, self.say(moved))

        held = {key: now()[key] for key in ("bugs", "events", "permissions")}
        update(lambda state: state.update(bugs=[], events={}, permissions={}))
        opening = self.tick(ISSUES)
        quiet = self.tick(ISSUES)
        step("the issues gate runs the installed faker-kit issues and faker-kit status: it wakes on its first tick and skips the next while nothing is listed",
             self.woke(opening, NOT_RECORDED) and opening.stdout.splitlines()[2] == issues_json([]) and self.silent(quiet), self.say(opening))
        bug = {"number": 21, "state": "open", "labels": ["faker:fix"], "title": "t", "body": "b"}
        label = {"id": 301, "event": "labeled", "label": {"name": "faker:fix"}, "actor": {"login": "alice"}}
        update(lambda state: state.update(bugs=[bug], events={"21": [label]}, permissions={"alice": {"permission": "write", "role_name": "write"}}))
        listed = self.tick(ISSUES)
        update(lambda state: state.update(held))
        step("an issue a collaborator labeled makes the real faker-kit issues list it, which wakes the issues gate with the listing",
             self.woke(listed, "faker-kit issues lists 1 issue(s) to handle") and listed.stdout.splitlines()[2] == issues_json([REAL_LABELED]), self.say(listed))
        self.reset()

    def babysit(self) -> None:
        step = self.run.step
        self.reset()
        self.serve(STATUS)
        first = self.tick(BABYSIT)
        stored = self.state(BABYSIT) if self.states[BABYSIT].is_file() else {}
        folder = self.states[BABYSIT].parent
        step("the babysit gate's first tick wakes the agent with the status lines as its Script Output, after one faker-kit status and no other command",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[1:] == ["faker-kit status:", *STATUS] and self.calls() == ["status"], self.say(first))
        step("it stores the status fingerprint and the wake time as an owner-only file, with no temp file left beside it",
             set(stored) == {"fingerprint", "woke_at"} and stored["fingerprint"] == fingerprint_of(STATUS) and bool(FINGERPRINT.match(stored["fingerprint"]))
             and abs(stored["woke_at"] - time.time()) < MARGIN and stat.S_IMODE(self.states[BABYSIT].stat().st_mode) == 0o600
             and stat.S_IMODE(folder.stat().st_mode) == 0o700 and [p.name for p in folder.iterdir()] == [self.states[BABYSIT].name], json.dumps(stored))

        kept = self.snapshot(BABYSIT)
        unchanged = self.tick(BABYSIT)
        step("an unchanged status prints the skip gate as its last line, exits 0, and writes nothing", self.silent(unchanged) and self.snapshot(BABYSIT) == kept, self.say(unchanged))
        self.serve("\n\n" + "\n".join(f"  {line}   " for line in STATUS) + "\n\n")
        spaced = self.tick(BABYSIT)
        step("a status that differs only in blank lines and surrounding spaces is the same signal and stays quiet", self.silent(spaced) and self.snapshot(BABYSIT) == kept, self.say(spaced))

        changed = [STATUS[0], STATUS[1].replace("pending", "failed")]
        self.serve(changed)
        woken = self.tick(BABYSIT)
        step("a changed status wakes the agent with the new lines and stores the new fingerprint",
             self.woke(woken, CHANGED) and woken.stdout.splitlines()[2:] == changed and self.state(BABYSIT)["fingerprint"] == fingerprint_of(changed), self.say(woken))

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
        step("an unchanged status wakes the agent once six hours have passed since the last wake, and stores the new wake time",
             self.woke(elapsed, HEARTBEAT_REASON) and abs(self.state(BABYSIT)["woke_at"] - time.time()) < MARGIN, self.say(elapsed))
        self.set_woke_at(BABYSIT, time.time() + HEARTBEAT)
        skewed = self.tick(BABYSIT)
        step("a wake time in the future, as after a clock change, wakes the agent instead of keeping it quiet for good",
             self.woke(skewed, HEARTBEAT_REASON) and self.state(BABYSIT)["woke_at"] < time.time() + MARGIN, self.say(skewed))

        damaged = {"not json": "{not json", "a list": "[]", "no fingerprint": json.dumps({"woke_at": time.time()}), "a text time": json.dumps({"fingerprint": "x", "woke_at": "now"}),
                   "a boolean time": json.dumps({"fingerprint": "x", "woke_at": True}), "a time that is not finite": '{"fingerprint": "x", "woke_at": NaN}'}
        rewritten = {}
        for label, text in damaged.items():
            self.states[BABYSIT].write_text(text)
            done = self.tick(BABYSIT)
            rewritten[label] = self.woke(done, NOT_RECORDED) and bool(FINGERPRINT.match(str(self.state(BABYSIT).get("fingerprint"))))
        step("a state file that is unreadable or malformed counts as no earlier tick: the agent wakes and the file is written whole again", all(rewritten.values()), json.dumps(rewritten))

        other = self.hermes / "profiles" / "other-bot"
        other.mkdir(exist_ok=True)
        before = self.snapshot(BABYSIT)
        elsewhere = self.tick(BABYSIT, other)
        step("the state belongs to the profile Hermes runs the gate in: another profile gets its own file and this profile's file is untouched",
             self.woke(elsewhere, NOT_RECORDED) and (self.gates / "other-bot" / f"{BABYSIT}.json").is_file() and self.snapshot(BABYSIT) == before, self.say(elsewhere))

        arranged: dict[str, tuple[Callable[[], None], str]] = {
            "faker-kit status exits non-zero": (lambda: self.program("status", "\n".join(STATUS), code=3, err="gh: Internal Server Error (HTTP 500)"), "faker-kit status exited 3"),
            "faker-kit status prints nothing": (lambda: self.program("status", ""), "faker-kit status printed nothing"),
        }
        outcome = {}
        for label, (arrange, message) in arranged.items():
            arrange()
            self.reset()
            fresh = self.tick(BABYSIT)
            outcome[f"{label}, with no state"] = self.failed(fresh, message) and not self.states[BABYSIT].exists() and not self.gates.exists()
            self.states[BABYSIT].parent.mkdir(parents=True)
            self.states[BABYSIT].write_text(json.dumps({"fingerprint": "0" * 64, "woke_at": time.time() - HEARTBEAT - MARGIN}))
            kept = self.snapshot(BABYSIT)
            stale = self.tick(BABYSIT)
            outcome[f"{label}, with the heartbeat due"] = self.failed(stale, message) and self.snapshot(BABYSIT) == kept
        step("when faker-kit status fails or prints nothing the gate exits non-zero with a short message and nothing on stdout, and neither creates nor changes the state, heartbeat due or not",
             all(outcome.values()), json.dumps(outcome))
        aside = self.wrapper.with_name("faker-kit.aside")
        self.reset()
        self.wrapper.rename(aside)
        missing = self.tick(BABYSIT)
        aside.rename(self.wrapper)
        step("a wrapper that cannot be run fails the tick the same way, naming only the error's type, and stores nothing",
             self.failed(missing, "faker-kit status did not run: FileNotFoundError") and not self.states[BABYSIT].exists(), self.say(missing))

    def issues(self) -> None:
        step = self.run.step
        self.reset()
        self.serve(STATUS)
        first = self.tick(ISSUES)
        step("the issues gate's first tick wakes the agent with the issues JSON and the status lines, after one faker-kit issues and one faker-kit status",
             self.woke(first, NOT_RECORDED) and first.stdout.splitlines()[1:] == ["faker-kit issues:", issues_json([]), "faker-kit status:", *STATUS]
             and self.calls()[-2:] == ["issues", "status"] and bool(FINGERPRINT.match(self.state(ISSUES)["fingerprint"])), self.say(first))
        kept = self.snapshot(ISSUES)
        quiet = self.tick(ISSUES)
        step("no issue and an unchanged status skip the turn and write nothing", self.silent(quiet) and self.snapshot(ISSUES) == kept, self.say(quiet))
        self.serve(STATUS, issues_json([LABELED]))
        woken_at = self.state(ISSUES)["woke_at"] - 60
        self.set_woke_at(ISSUES, woken_at)
        listed = self.tick(ISSUES)
        step("a listed issue wakes the agent with it even when the status and heartbeat alone would not, and stores the wake time",
             self.woke(listed, "faker-kit issues lists 1 issue(s) to handle") and listed.stdout.splitlines()[2] == issues_json([LABELED])
             and self.state(ISSUES)["woke_at"] > woken_at, self.say(listed))
        self.serve([*STATUS, f"#3 | by carol | ready | mergeable | head {'c' * 40} | ready to merge"])
        moved = self.tick(ISSUES)
        step("with no issue listed, a changed status still wakes the issues job", self.woke(moved, CHANGED), self.say(moved))
        self.set_woke_at(ISSUES, time.time() - HEARTBEAT - MARGIN)
        beat = self.tick(ISSUES)
        step("with no issue listed and an unchanged status, six hours since the last wake wakes the issues job", self.woke(beat, HEARTBEAT_REASON), self.say(beat))
        step("the issues gate keeps its own state: the babysit gate's file was never created by it", not self.states[BABYSIT].exists() and self.states[ISSUES].is_file(),
             ", ".join(sorted(p.name for p in self.states[ISSUES].parent.iterdir())))

        self.serve(STATUS)
        self.tick(ISSUES)
        self.set_woke_at(ISSUES, time.time() - HEARTBEAT - MARGIN)
        kept = self.snapshot(ISSUES)
        arranged: dict[str, tuple[Callable[[], None], str]] = {
            "faker-kit issues exits non-zero": (lambda: self.program("issues", issues_json([LABELED]), code=1, err="faker-kit: refused: no collaborator"), "faker-kit issues exited 1"),
            "faker-kit issues prints no JSON": (lambda: self.program("issues", "no issues here"), "faker-kit issues printed no JSON"),
            "faker-kit issues prints a list": (lambda: self.program("issues", "[]"), "faker-kit issues printed an unexpected report shape"),
            "faker-kit issues prints issues that are not a list": (lambda: self.program("issues", json.dumps({"issues": "none"})), "faker-kit issues printed an unexpected report shape"),
            "faker-kit status exits non-zero": (lambda: self.program("status", "", code=1, err="faker-kit: refused: gh is missing"), "faker-kit status exited 1"),
            "faker-kit status prints nothing": (lambda: self.program("status", ""), "faker-kit status printed nothing"),
        }
        outcome = {}
        for label, (arrange, message) in arranged.items():
            self.serve(STATUS)
            arrange()
            done = self.tick(ISSUES)
            outcome[label] = self.failed(done, message) and self.snapshot(ISSUES) == kept
        step("when faker-kit issues or faker-kit status fails or prints an unexpected shape, the issues gate exits non-zero with a short message and leaves the state as it was, heartbeat due or not",
             all(outcome.values()), json.dumps(outcome))
        self.reset()


def wake_scenario(run: Run, hermes: Path, bot: str, env: dict[str, str], work: Path) -> None:
    gates = Gates(run, hermes, bot, env, work)
    gates.real_command()
    with gates.fake_wrapper():
        gates.babysit()
        gates.issues()
