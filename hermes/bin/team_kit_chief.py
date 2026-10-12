#!/usr/bin/env python3
"""team-kit-chief: the chief of staff's only command. It routes across projects.

  projects                                  every project: its bot, its repository, and any andon cord
  status                                    one line per project: in flight, waiting on the owner, done today
  show          --project P --path F        a file from a project's default branch, such as AGENTS.md
  digest        --file F                    the SHA-256 of a draft in this bot's drafts folder
  propose       --project P --file F --sha256 H
                                            copy one draft into that project bot's inbox; H must be F's digest now,
                                            then start that bot's team-kit-tick so the notice arrives in about a minute
  andon                                     the machine's and every project's andon cord

The chief of staff routes the owner's intent: it says which project bot gets which part, and
can hand one proposal to that bot. The proposal is a .md file of at most 20,000 bytes that the chief
wrote under ~/.hermes/team-kit/drafts/<chief bot>/ (symlinks are refused). Each owner approval of
`propose` names the file's SHA-256, so it covers exactly that content. It never opens an issue,
launches, or writes anywhere else.
Its GitHub token needs read access to every project's repository and nothing more.

Only what trusted logins wrote reaches the chief: unit titles from issues a trusted author
opened, and files from the default branch. Issue bodies and comments never do.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from install_config import cron_jobs
from team_kit_andon import Andon
from team_kit_env import EnvFileError, use_bot_env
from team_kit_guard.policy import Bot, Policy, PolicyError, load_policy
from team_kit_proposals import ProposalError, file_digest, write_proposal

REPO_PATH = re.compile(r"\A(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
TASK_LABEL = "agent-task"
SILENT = "[SILENT]"
DAY = 24 * 3600
MAX_FILE = 60_000
# Each unit's state, in the order the line names them.
STATES = (("agent:running", "running"), ("agent:verifying", "verifying"), ("agent:needs-human", "gate"), ("agent:review", "merge"))

Runner = Callable[..., str]
Waker = Callable[[str], bool]
TICK_JOB = "team-kit-tick"
WAKE_TIMEOUT_SECONDS = 30
# All the detached tick inherits: no token, no session, and no HERMES_HOME of the chief.
WAKE_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")


class Refused(Exception):
    pass


def run_gh(args: list[str], input: str | None = None) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, input=input)
    if done.returncode != 0:
        raise Refused(f"gh {args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def wake_tick(bot: str) -> bool:
    """Start the bot's tick now, detached. False when its tick is missing or paused, or Hermes cannot start.

    `hermes cron run` runs the job inline, a model turn that can take minutes, so the chief does not wait
    for it. The run gets no secret from the chief: Hermes loads the target profile's own .env.
    """
    try:
        listed = subprocess.run(["hermes", "-p", bot, "cron", "list", "--all"], capture_output=True, text=True, timeout=WAKE_TIMEOUT_SECONDS)
        job = cron_jobs(listed.stdout).get(TICK_JOB) if listed.returncode == 0 else None
        if not job or not job["enabled"]:
            return False
        env = {k: v for k, v in os.environ.items() if k in WAKE_ENV}
        subprocess.Popen(["hermes", "-p", bot, "cron", "run", job["id"]], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


def current_bot() -> str:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.name if home.parent.name == "profiles" else "default"


def timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


class Chief:
    def __init__(self, policy: Policy, run: Runner, now: Callable[[], float] = time.time, wake: Waker = wake_tick):  # noqa: ANN204
        self.policy, self.run, self.now, self.wake = policy, run, now, wake
        self.andon = Andon(policy.home, now)

    def project_bots(self) -> list[Bot]:
        return sorted(self.policy.projects().values(), key=lambda b: b.project or "")

    def project(self, slug: str) -> Bot:
        bot = self.policy.projects().get(slug)
        if bot is None:
            raise Refused(f"no project {slug}; `team-kit-chief projects` lists them")
        return bot

    def trusted_author(self, issue: dict) -> bool:
        return str((issue.get("author") or {}).get("login") or "").lower() in self.policy.trusted_authors

    def cord(self, bot: Bot) -> str | None:
        cord = self.andon.project(bot.name)
        return cord.reason if cord else None

    def projects(self) -> dict:
        machine = self.andon.machine()
        return {
            "machine_andon": machine.reason if machine else None,
            "projects": [{"project": b.project, "bot": b.name, "repo": b.repo, "andon": self.cord(b)} for b in self.project_bots()],
        }

    def units(self, repo: str, state: str) -> list[dict]:
        args = ["issue", "list", "-R", repo, "--label", TASK_LABEL, "--state", state,
                "--json", "number,title,labels,author,closedAt,stateReason", "--limit", "200"]
        if state == "closed":
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now() - DAY))
            args += ["--search", f"closed:>={since}"]
        return sorted(json.loads(self.run(args)), key=lambda i: int(i["number"]))

    def title(self, issue: dict) -> str:
        """A unit's title only if a trusted author wrote it; anything else could steer the router."""
        if not self.trusted_author(issue):
            return f"#{issue['number']} (opened by someone else)"
        title = " ".join(str(issue.get("title") or "").split())[:100]
        return f"#{issue['number']} {title}"

    def project_line(self, bot: Bot) -> str:
        try:
            open_units, closed = self.units(bot.repo, "open"), self.units(bot.repo, "closed")  # type: ignore[arg-type]  # team-kit baseline
        except Refused as err:
            return f"{bot.project} ({bot.name}, {bot.repo}): unreadable: {err}"
        flight, waiting, done = [], [], []  # type: ignore[var-annotated]  # team-kit baseline
        for issue in open_units:
            labels = {label["name"] for label in issue.get("labels") or []}
            state = next((name for label, name in STATES if label in labels), "queued")
            (waiting if state in ("gate", "merge") else flight).append(f"{self.title(issue)} ({state})")
        for issue in closed:
            closed_at = timestamp(issue.get("closedAt")) or 0
            if closed_at >= self.now() - DAY and str(issue.get("stateReason") or "").upper() in ("", "COMPLETED"):
                done.append(self.title(issue))
        part = lambda name, items: f"{name} {len(items)}" + (f" ({'; '.join(items)})" if items else "")  # noqa: E731
        cord = self.cord(bot)
        head = f"{bot.project} ({bot.name}, {bot.repo})" + (f" ANDON: {cord}" if cord else "")
        return f"{head}: {part('in flight', flight)}; {part('waiting on you', waiting)}; {part('done today', done)}."

    def status(self) -> list[str]:
        bots = self.project_bots()
        if not bots:
            return [SILENT]
        machine = self.andon.machine()
        lines = [f"ANDON for the machine: {machine.reason}. Nothing new starts in any project."] if machine else []
        return lines + [self.project_line(bot) for bot in bots]

    def show(self, slug: str, path: str) -> str:
        bot = self.project(slug)
        if not REPO_PATH.match(path):
            raise Refused(f"{path!r} is not a plain path inside the repository")
        try:
            data = json.loads(self.run(["api", f"repos/{bot.repo}/contents/{path}"]))
            text = base64.b64decode(data["content"]).decode()
        except (Refused, ValueError, TypeError, KeyError):
            raise Refused(f"{path} is not a readable file in {bot.repo}") from None
        return text if len(text) <= MAX_FILE else text[:MAX_FILE] + f"\n[cut at {MAX_FILE} characters]\n"

    def propose(self, slug: str, path: str, sha256: str, from_bot: str) -> dict:
        bot = self.project(slug)
        try:
            out = write_proposal(self.policy.home, bot.name, slug, bot.repo or "", path, from_bot, self.now(), sha256)
        except ProposalError as err:
            raise Refused(str(err)) from None
        then = (f"{bot.name} briefs and dispatches it by itself (ask_owner is false)" if not self.policy.ask_owner
                else f"the owner says \"brief it\" in {bot.name}'s Bot Chat")
        if self.wake(bot.name):
            out["delivery"] = f"{bot.name}'s {TICK_JOB} started now; the notice reaches its Bot Chat within about a minute; then {then}"
        else:
            out["delivery"] = (f"{bot.name}'s {TICK_JOB} could not be started now (paused or missing); the notice arrives at its next tick; then {then}. "
                               f"If the bot is not active yet: team-kit-admin activate --bot {bot.name}")
        return out

    def digest(self, path: str, from_bot: str) -> dict:
        try:
            return {"file": os.path.basename(path), "sha256": file_digest(self.policy.home, from_bot, path)}  # noqa: PTH119
        except ProposalError as err:
            raise Refused(str(err)) from None

    def andon_state(self) -> dict:
        machine = self.andon.machine()
        return {"machine": machine.reason if machine else None,
                "projects": {b.project: self.cord(b) for b in self.project_bots() if self.cord(b)}}


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="team-kit-chief")
    sub = top.add_subparsers(dest="command", required=True)
    for name in ("projects", "status", "andon"):
        sub.add_parser(name)
    cmd = sub.add_parser("show")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--path", required=True)
    cmd = sub.add_parser("digest")
    cmd.add_argument("--file", required=True)
    cmd = sub.add_parser("propose")
    cmd.add_argument("--project", required=True)
    cmd.add_argument("--file", required=True)
    cmd.add_argument("--sha256", required=True)
    return top


def bot_env(policy: Policy, me: Bot) -> None:
    try:
        use_bot_env(policy.home.parent / "profiles" / me.name)
    except EnvFileError as err:
        raise Refused(str(err)) from None


def main(argv: list[str] | None = None, run: Runner = run_gh, bot: str | None = None, now: Callable[[], float] = time.time,
         wake: Waker = wake_tick) -> int:
    args = parser().parse_args(argv)
    try:
        policy = load_policy()
        me = policy.bot(bot or current_bot())
        if me.role != "chief":
            raise Refused(f"{me.name} is a {me.role} bot; team-kit-chief is for the chief of staff")  # noqa: TRY301
        bot_env(policy, me)
        chief = Chief(policy, run, now, wake)
        if args.command == "status":
            print("\n".join(chief.status()))
            return 0
        if args.command == "show":
            print(chief.show(args.project, args.path), end="")
            return 0
        if args.command == "propose":
            out = chief.propose(args.project, args.file, args.sha256, me.name)
        elif args.command == "digest":
            out = chief.digest(args.file, me.name)
        else:
            out = chief.projects() if args.command == "projects" else chief.andon_state()
    except (Refused, PolicyError) as err:
        print(f"team-kit-chief: refused: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001 - the bot gets one refusal line, never a traceback
        print(f"team-kit-chief: refused: unexpected {type(err).__name__}; nothing more was done", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
