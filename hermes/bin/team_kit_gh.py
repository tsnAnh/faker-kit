#!/usr/bin/env python3
"""team-kit-gh: the only GitHub access a project bot has, and only for its own project's repositories.

  project                                                   this bot's project, repositories, home repository, and running units
  open-issue    --repo R --card C --size S --brief-file F   check the pin and the brief, open the issue
  launch        --repo R --issue N --size S                 start the cloud coding agent for an opened unit
  report-status --repo R --issue N                          the latest report's parsed header, nothing else
  read          --repo R --issue N | --pr N                 the body and comments (untrusted; the guard wraps it)
  show          --repo R --path P                           a file from the default branch (the owner's repo; trusted)
  pr-state      --repo R --pr N                             state, merged, and head SHA of a pull request, nothing else
  needs-human   --repo R --issue N                          add the agent:needs-human label
  kit-check     --repo R                                    compare this machine's kit commit with kit_ref

Which bot is running comes from the Hermes profile (HERMES_HOME). Which repositories it may
touch comes from bots.yaml. The guard lets a project bot run this command and nothing else.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent / "lib", HERE.parent / "plugin"):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from team_kit_cloud import Cloud, CloudError  # noqa: E402
from team_kit_contracts import (  # noqa: E402
    LAUNCHED_MARKER,
    REPORT_MARKER,
    UNIT_ATTEMPTS,
    ContractError,
    launch_argv,
    parse_brief,
    parse_depends,
    parse_report,
)
from team_kit_guard.policy import Bot, Policy, PolicyError, load_policy  # noqa: E402

SHA = re.compile(r"\A[0-9a-f]{40}\Z")
SHORT_SHA = re.compile(r"\A[0-9a-f]{7,40}\Z")
REPO_PATH = re.compile(r"\A(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
TASK_LABEL = "agent-task"
RUNNING_LABEL = "agent:running"
HUMAN_LABEL = "agent:needs-human"

Runner = Callable[..., str]


class Refused(Exception):
    pass


def run_gh(args: list[str], input: str | None = None) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True, input=input)
    if done.returncode != 0:
        raise Refused(f"gh {args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def current_bot() -> str:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    return home.name if home.parent.name == "profiles" else "default"


def kit_commit(policy: Policy) -> str:
    done = subprocess.run(["git", "-C", str(policy.home / "kit"), "rev-parse", "HEAD"], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"no kit checkout at {policy.home / 'kit'}; run hermes/install.sh")
    return done.stdout.strip()


def in_flight(run: Runner, repo: str) -> int:
    issues = json.loads(run(["issue", "list", "-R", repo, "--label", RUNNING_LABEL, "--state", "open", "--json", "number", "--limit", "200"]))
    return len(issues)


# Each project bot's GitHub login sees only its own repositories, so a bot cannot count another
# project's running units. Instead every project bot writes its own count to in-flight/<bot>.json
# whenever it counts (team-kit-gh project, which every drain runs first, and launch), and reads the
# other bots' files for the machine total. team-kit-admin status, which sees every repository,
# refreshes all of them. A launch holds a machine-wide lock from counting to labelling.
# A count older than this is ignored: no cloud session runs that long.
IN_FLIGHT_FRESH_SECONDS = 24 * 3600


class InFlight:
    def __init__(self, policy: Policy, bot: Bot, run: Runner, now: Callable[[], float] = time.time):
        self.policy, self.bot, self.run, self.now = policy, bot, run, now
        self.directory = policy.home / "in-flight"

    @contextlib.contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with open(self.directory / ".lock", "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def count_own(self) -> dict[str, int]:
        counts = {repo: in_flight(self.run, repo) for repo in self.bot.repos}
        self.write(counts)
        return counts

    def write(self, counts: dict[str, int]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{self.bot.name}.json"
        tmp = target.with_name(f".{target.name}.{os.getpid()}")
        tmp.write_text(json.dumps({"at": self.now(), "repos": counts}))
        tmp.replace(target)

    def others(self) -> int:
        total = 0
        for name, other in self.policy.bots.items():
            if name == self.bot.name or other.role != "project":
                continue
            try:
                data = json.loads((self.directory / f"{name}.json").read_text())
                if self.now() - float(data["at"]) <= IN_FLIGHT_FRESH_SECONDS:
                    total += sum(int(n) for n in data["repos"].values())
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue
        return total


class Gh:
    def __init__(self, policy: Policy, bot: Bot, repo: str, run: Runner):
        if bot.role != "project":
            raise Refused(f"{bot.name} is a {bot.role} bot; team-kit-gh is for project bots")
        if not bot.owns(repo):
            raise Refused(f"{repo} is not a repository of project {bot.project}")
        self.policy, self.bot, self.run = policy, bot, run
        self.repo = next(r for r in bot.repos if r.lower() == repo.lower())

    def file(self, path: str) -> str:
        if not REPO_PATH.match(path):
            raise Refused(f"{path!r} is not a plain path inside the repository")
        try:
            data = json.loads(self.run(["api", f"repos/{self.repo}/contents/{path}"]))
            return base64.b64decode(data["content"]).decode()
        except (ValueError, TypeError, KeyError):
            raise Refused(f"{path} is not a readable file in {self.repo}") from None

    def dispatch_config(self) -> dict:
        try:
            config = json.loads(self.file(".agents/dispatch.json"))
        except ValueError:
            raise Refused(".agents/dispatch.json is not valid JSON") from None
        if not isinstance(config, dict) or config.get("backend") not in ("claude", "codex"):
            raise Refused(".agents/dispatch.json backend must be claude or codex")
        return config

    def kit_check(self, local_commit: str, config: dict | None = None) -> dict:
        pinned = str((config or self.dispatch_config()).get("kit_ref", ""))
        if not SHA.match(pinned):
            raise Refused("kit_ref in .agents/dispatch.json must be a full 40-character commit")
        if local_commit != pinned:
            raise Refused(f"kit commit {local_commit[:12]} differs from kit_ref {pinned[:12]}; update one side first")
        return {"kit": local_commit, "kit_ref": pinned}

    def depends(self, brief) -> list[tuple[str, int]]:
        refs = parse_depends(brief, self.repo)
        outside = [f"{r}#{n}" for r, n in refs if not self.bot.owns(r)]
        if outside:
            raise Refused(f"Depends on names issues outside project {self.bot.project}: {', '.join(outside)}")
        return refs

    def open_issue(self, card: str, size: str, brief_text: str, local_commit: str) -> dict:
        if size not in UNIT_ATTEMPTS:
            raise Refused(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
        self.kit_check(local_commit)
        standing = self.file(".agents/dispatch.md") if self.policy.require_brief_marker else None
        brief = parse_brief(brief_text, standing_orders=standing)
        if brief.card != card:
            raise Refused(f"brief marker names card {brief.card}, not {card}")
        refs = self.depends(brief)
        url = self.run(["issue", "create", "-R", self.repo, "--title", brief.title, "--body", brief_text, "--label", TASK_LABEL]).strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        return {"issue": number, "url": url, "card": card, "depends": [f"{r}#{n}" for r, n in refs]}

    def issue(self, number: int, repo: str | None = None) -> dict:
        return json.loads(self.run(["api", f"repos/{repo or self.repo}/issues/{int(number)}"]))

    def waiting_for(self, refs: list[tuple[str, int]]) -> list[str]:
        waiting = []
        for repo, number in refs:
            dep = self.issue(number, repo)
            if dep.get("state") != "closed" or dep.get("state_reason") not in (None, "completed"):
                waiting.append(f"{repo}#{number}")
        return waiting

    def launch(self, number: int, size: str, local_commit: str, cloud: Cloud, now: Callable[[], float] = time.time) -> dict:
        if size not in UNIT_ATTEMPTS:
            raise Refused(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
        config = self.dispatch_config()
        self.kit_check(local_commit, config)
        issue = self.issue(number)
        labels = {str(label.get("name", "")).lower() for label in issue.get("labels") or []}
        author = str((issue.get("user") or {}).get("login") or "").lower()
        if "pull_request" in issue or issue.get("state") != "open" or TASK_LABEL not in labels:
            raise Refused(f"#{number} is not an open {TASK_LABEL} issue")
        if RUNNING_LABEL in labels:
            raise Refused(f"#{number} is already launched ({RUNNING_LABEL}); never launch a unit twice")
        if author not in {str(d).lower() for d in config.get("trusted_dispatchers") or []}:
            raise Refused(f"#{number} was written by {author or 'nobody'}, who is not in trusted_dispatchers")
        brief = parse_brief(issue.get("body") or "")
        waiting = self.waiting_for(self.depends(brief))
        if waiting:
            raise Refused(f"waiting for dependencies: {', '.join(waiting)} must be closed by a merged pull request")
        limit = int(config.get("max_in_flight", 1))
        argv = launch_argv(config["backend"], self.repo, number, size, config.get("codex_env"))
        owner, name = self.repo.split("/")
        workdir = self.policy.home / "checkouts" / f"{owner}__{name}" / str(number)
        counter = InFlight(self.policy, self.bot, self.run, now)
        with counter.lock():
            counts = counter.count_own()
            if counts[self.repo] >= limit:
                raise Refused(f"repo limit: {limit} unit(s) already in flight in {self.repo} (max_in_flight); wait for one to merge")
            total = sum(counts.values()) + counter.others()
            if total >= self.policy.max_in_flight_total:
                raise Refused(f"machine limit: {total} unit(s) already in flight on this machine (max_in_flight_total); wait for one to merge")
            self.run(["issue", "edit", str(number), "-R", self.repo, "--add-label", RUNNING_LABEL])
            counter.write({**counts, self.repo: counts[self.repo] + 1})
        try:
            session = cloud.start(config["backend"], self.repo, argv, workdir)
        except Exception as err:
            # Any failure, a missing `claude` binary included, must free the unit again.
            self.run(["issue", "edit", str(number), "-R", self.repo, "--remove-label", RUNNING_LABEL])
            counter.write(counts)
            reason = str(err) if isinstance(err, (CloudError, Refused)) else type(err).__name__ + (f": {err.filename}" if getattr(err, "filename", None) else "")
            raise Refused(f"launch failed: {reason}") from err
        body = f"{LAUNCHED_MARKER}\nsession: {session}\nbackend: {config['backend']}\n"
        self.run(["issue", "comment", str(number), "-R", self.repo, "--body", body])
        return {"issue": number, "card": brief.card, "backend": config["backend"], "session": session}

    def comments(self, issue: int) -> list[dict]:
        return json.loads(self.run(["api", "--paginate", f"repos/{self.repo}/issues/{issue}/comments"]))

    def report_status(self, issue: int) -> dict:
        authors = self.policy.trusted_authors
        reports = [
            c for c in self.comments(issue)
            if (c.get("body") or "").lstrip().startswith(REPORT_MARKER) and ((c.get("user") or {}).get("login") or "").lower() in authors
        ]
        if not reports:
            return {"issue": issue, "status": "none"}
        try:
            report = parse_report(reports[-1]["body"])
        except ContractError:
            return {"issue": issue, "status": "unreadable"}
        pr_url = re.compile(rf"\Ahttps://github\.com/{re.escape(self.repo)}/pull/[0-9]+\Z", re.I)
        return {
            "issue": issue,
            "status": report.status,
            "pr": report.pr if report.pr and pr_url.match(report.pr) else None,
            "head": report.head if report.head and SHORT_SHA.match(report.head) else None,
        }

    def pr_state(self, pr: int) -> dict:
        data = json.loads(self.run(["pr", "view", str(pr), "-R", self.repo, "--json", "state,mergedAt,headRefOid"]))
        head = str(data.get("headRefOid") or "")
        return {"pr": pr, "state": str(data.get("state", "")).lower(), "merged": bool(data.get("mergedAt")), "head": head if SHA.match(head) else None}

    def read(self, issue: int | None, pr: int | None) -> str:
        kind, number = ("pr", pr) if pr else ("issue", issue)
        return self.run([kind, "view", str(number), "-R", self.repo, "--comments"])

    def needs_human(self, issue: int) -> dict:
        """The agent stopped (it reported, or never started), so its slot is freed for queued units."""
        self.run(["issue", "edit", str(issue), "-R", self.repo, "--add-label", HUMAN_LABEL, "--remove-label", RUNNING_LABEL])
        return {"issue": issue, "label": HUMAN_LABEL, "removed": RUNNING_LABEL}


def brief_text(policy: Policy, bot: Bot, path: str) -> str:
    """A brief is read only from the bot's own briefs directory, so no other file can become an issue body."""
    briefs = (policy.home / "briefs" / bot.name).resolve()
    target = Path(os.path.expanduser(path)).resolve()
    if target.parent != briefs or target.suffix != ".md":
        raise Refused(f"--brief-file must be a .md file in {briefs}")
    try:
        return target.read_text()
    except OSError:
        raise Refused(f"cannot read {target}") from None


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="team-kit-gh")
    sub = top.add_subparsers(dest="command", required=True)
    sub.add_parser("project")
    for name in ("open-issue", "launch", "report-status", "read", "show", "pr-state", "needs-human", "kit-check"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True)
        if name == "open-issue":
            cmd.add_argument("--card", required=True)
            cmd.add_argument("--size", required=True, choices=list(UNIT_ATTEMPTS))
            cmd.add_argument("--brief-file", required=True)
        elif name == "launch":
            cmd.add_argument("--issue", type=int, required=True)
            cmd.add_argument("--size", required=True, choices=list(UNIT_ATTEMPTS))
        elif name == "read":
            cmd.add_argument("--issue", type=int)
            cmd.add_argument("--pr", type=int)
        elif name == "show":
            cmd.add_argument("--path", required=True)
        elif name == "pr-state":
            cmd.add_argument("--pr", type=int, required=True)
        elif name != "kit-check":
            cmd.add_argument("--issue", type=int, required=True)
    return top


def main(argv: list[str] | None = None, run: Runner = run_gh, bot: str | None = None, cloud: Cloud | None = None, commit: Callable[[Policy], str] = kit_commit) -> int:
    args = parser().parse_args(argv)
    try:
        policy = load_policy()
        me = policy.bot(bot or current_bot())
        if args.command == "project":
            if me.role != "project":
                raise Refused(f"{me.name} is a {me.role} bot; team-kit-gh is for project bots")
            out = {"bot": me.name, "project": me.project, "repos": list(me.repos), "home": me.home}
            try:
                out["in_flight"] = InFlight(policy, me, run).count_own()
            except (Refused, ValueError) as err:
                # No GitHub login yet, or GitHub is down. The project itself is still known.
                out["in_flight"] = f"unknown: {err}"[:200]
            print(json.dumps(out))
            return 0
        gh = Gh(policy, me, args.repo, run)
        if args.command == "open-issue":
            text = brief_text(policy, me, args.brief_file)
            out = gh.open_issue(args.card, args.size, text, commit(policy))
        elif args.command == "launch":
            out = gh.launch(args.issue, args.size, commit(policy), cloud or Cloud(run_gh=run))
        elif args.command == "report-status":
            out = gh.report_status(args.issue)
        elif args.command == "read":
            if not (args.issue or args.pr):
                raise Refused("read needs --issue or --pr")
            print(gh.read(args.issue, args.pr))
            return 0
        elif args.command == "show":
            print(gh.file(args.path), end="")
            return 0
        elif args.command == "pr-state":
            out = gh.pr_state(args.pr)
        elif args.command == "needs-human":
            out = gh.needs_human(args.issue)
        else:
            out = gh.kit_check(commit(policy))
    except (Refused, ContractError, PolicyError) as err:
        print(f"team-kit-gh: refused: {err}", file=sys.stderr)
        return 1
    except Exception as err:  # noqa: BLE001 - the bot gets one refusal line, never a traceback
        print(f"team-kit-gh: refused: unexpected {type(err).__name__}; nothing more was done", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
