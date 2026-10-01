#!/usr/bin/env python3
"""team-kit-gh: the only GitHub access the dispatcher profile has.

  open-issue    --repo R --card C --size S --brief-file F   check the pin and the brief, open the issue
  report-status --repo R --issue N                          the latest report's parsed header, nothing else
  read          --repo R --issue N | --pr N                 the body and comments (untrusted; the guard wraps it)
  show          --repo R --path P                           a file from the default branch (the owner's repo; trusted)
  needs-human   --repo R --issue N                          add the agent:needs-human label
  kit-check     --repo R                                    compare this machine's kit commit with kit_ref
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
for candidate in (HERE, HERE.parent.parent / "webhook"):
    if (candidate / "team_kit_contracts.py").exists():
        sys.path.insert(0, str(candidate))
        break

from team_kit_contracts import (  # noqa: E402
    REPORT_MARKER,
    UNIT_ATTEMPTS,
    ContractError,
    launch_command,
    parse_brief,
    parse_report,
)

TASK_LABEL = "agent-task"
HUMAN_LABEL = "agent:needs-human"

Runner = Callable[[list[str]], str]


class Refused(Exception):
    pass


def run_gh(args: list[str]) -> str:
    done = subprocess.run(["gh", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"gh {args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def home() -> Path:
    return Path(os.environ.get("TEAM_KIT_HOME") or Path.home() / ".hermes" / "team-kit")


def guard_config() -> dict:
    import yaml

    return yaml.safe_load((home() / "guard.yaml").read_text()) or {}


def kit_commit() -> str:
    done = subprocess.run(["git", "-C", str(home() / "kit"), "rev-parse", "HEAD"], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"no kit checkout at {home() / 'kit'}; run hermes/install.sh")
    return done.stdout.strip()


class Gh:
    def __init__(self, repo: str, run: Runner, config: dict):
        if repo.lower() not in {str(r).lower() for r in config.get("repos") or []}:
            raise Refused(f"{repo} is not in guard.yaml repos")
        self.repo, self.run, self.config = repo, run, config

    def file(self, path: str) -> str:
        data = json.loads(self.run(["api", f"repos/{self.repo}/contents/{path}"]))
        return base64.b64decode(data["content"]).decode()

    def dispatch_config(self) -> dict:
        config = json.loads(self.file(".agents/dispatch.json"))
        if config.get("backend") not in ("claude", "codex"):
            raise Refused(".agents/dispatch.json backend must be claude or codex")
        return config

    def kit_check(self, local_commit: str) -> dict:
        pinned = str(self.dispatch_config().get("kit_ref", ""))
        if not pinned or not local_commit.startswith(pinned):
            raise Refused(f"kit commit {local_commit[:12]} differs from kit_ref {pinned[:12] or '(unset)'}; update one side first")
        return {"kit": local_commit, "kit_ref": pinned}

    def in_flight(self) -> int:
        return len(json.loads(self.run(["issue", "list", "-R", self.repo, "--label", TASK_LABEL, "--state", "open", "--json", "number", "--limit", "200"])))

    def open_issue(self, card: str, size: str, brief_text: str, local_commit: str) -> dict:
        if size not in UNIT_ATTEMPTS:
            raise Refused(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
        self.kit_check(local_commit)
        config = self.dispatch_config()
        if self.config.get("require_brief_marker", True):
            brief = parse_brief(brief_text, standing_orders=self.file(".agents/dispatch.md"))
            if brief.card != card:
                raise Refused(f"brief marker names card {brief.card}, not {card}")
        else:
            brief = parse_brief(brief_text)
        limit = int(config.get("max_in_flight", 1))
        if self.in_flight() >= limit:
            raise Refused(f"{limit} unit(s) already in flight (max_in_flight); wait for one to finish")
        url = self.run(["issue", "create", "-R", self.repo, "--title", brief.title, "--body", brief_text, "--label", TASK_LABEL]).strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        return {
            "issue": number,
            "url": url,
            "card": card,
            "launch": launch_command(config["backend"], self.repo, number, size, config.get("codex_env")),
        }

    def comments(self, issue: int) -> list[dict]:
        return json.loads(self.run(["api", "--paginate", f"repos/{self.repo}/issues/{issue}/comments"]))

    def report_status(self, issue: int) -> dict:
        authors = {str(a).lower() for a in self.config.get("trusted_authors") or []}
        reports = [
            c for c in self.comments(issue)
            if (c.get("body") or "").lstrip().startswith(REPORT_MARKER) and ((c.get("user") or {}).get("login") or "").lower() in authors
        ]
        if not reports:
            return {"issue": issue, "status": "none"}
        try:
            report = parse_report(reports[-1]["body"])
        except ContractError as err:
            return {"issue": issue, "status": "unreadable", "why": str(err)}
        return {"issue": issue, "status": report.status, "pr": report.pr, "head": report.head}

    def read(self, issue: int | None, pr: int | None) -> str:
        kind, number = ("pr", pr) if pr else ("issue", issue)
        return self.run([kind, "view", str(number), "-R", self.repo, "--comments"])

    def needs_human(self, issue: int) -> dict:
        self.run(["issue", "edit", str(issue), "-R", self.repo, "--add-label", HUMAN_LABEL])
        return {"issue": issue, "label": HUMAN_LABEL}


def main(argv: list[str] | None = None, run: Runner = run_gh) -> int:
    parser = argparse.ArgumentParser(prog="team-kit-gh")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("open-issue", "report-status", "read", "show", "needs-human", "kit-check"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True)
        if name == "open-issue":
            cmd.add_argument("--card", required=True)
            cmd.add_argument("--size", required=True, choices=list(UNIT_ATTEMPTS))
            cmd.add_argument("--brief-file", required=True)
        elif name == "read":
            cmd.add_argument("--issue", type=int)
            cmd.add_argument("--pr", type=int)
        elif name == "show":
            cmd.add_argument("--path", required=True)
        elif name != "kit-check":
            cmd.add_argument("--issue", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        gh = Gh(args.repo, run, guard_config())
        if args.command == "open-issue":
            text = Path(os.path.expanduser(args.brief_file)).read_text()
            out = gh.open_issue(args.card, args.size, text, kit_commit())
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
        elif args.command == "needs-human":
            out = gh.needs_human(args.issue)
        else:
            out = gh.kit_check(kit_commit())
    except (Refused, ContractError) as err:
        print(f"team-kit-gh: refused: {err}", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
