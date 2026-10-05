#!/usr/bin/env python3
"""team-kit-gh: the only GitHub access a project bot has, and only for its project's one repository.

  project                                          this bot's project and repository
  dispatch      --unit U --size S --brief-file F   open the unit's issue, then launch it or queue it
  launch        --issue N                          launch one queued or gated unit now (the owner asked)
  verify        --issue N                          verify a unit's pull request again, without redoing its work
  tick                                             launch queued units, open gates, free slots; notices only
  status                                           the three-line digest
  report-status --issue N                          the latest report's parsed header, nothing else
  pr-state      --pr N                             state, merged, head SHA, and checks of a pull request
  read          --issue N | --pr N                 the body and comments (untrusted; the guard wraps it)
  show          --path P                           a file from the default branch (the owner's repo; trusted)
  kit-check                                        compare this machine's kit commit with kit_ref and the vendored kit

GitHub holds every unit's state, as labels on its issue:

  agent-task          queued: waiting for its dependencies or a free slot
  agent:running       a cloud coding agent is working it
  agent:verifying     the agent reported done and the checks passed; a separate verifier session
                      is checking its pull request
  agent:review        verified (or verification is off) and the checks passed; the owner merges
  agent:needs-human   a gate: the agent stopped and needs the owner
  (closed)            done: its pull request merged

Which bot is running comes from the Hermes profile (HERMES_HOME). Its repository comes from
bots.yaml; a project is one repository. The guard lets a project bot run this command and nothing else.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
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
    DEFAULT_ACCEPTED,
    LAUNCHED_MARKER,
    REPORT_MARKER,
    UNIT_ATTEMPTS,
    VERDICT_MARKER,
    VERIFICATIONS,
    VERIFIER_BACKENDS,
    VERIFY_LAUNCHED_MARKER,
    ContractError,
    launch_argv,
    parse_brief,
    parse_depends,
    parse_report,
    parse_size,
    parse_verdict,
    size_marker,
    verdict_signed,
    verify_argv,
)
from team_kit_guard.policy import Bot, Policy, PolicyError, load_policy  # noqa: E402

SHA = re.compile(r"\A[0-9a-f]{40}\Z")
SHORT_SHA = re.compile(r"\A[0-9a-f]{7,40}\Z")
REPO_PATH = re.compile(r"\A(?!.*(?:^|/)\.\.?(?:/|$))[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
TASK_LABEL = "agent-task"
RUNNING_LABEL = "agent:running"
VERIFYING_LABEL = "agent:verifying"
REVIEW_LABEL = "agent:review"
HUMAN_LABEL = "agent:needs-human"
VENDOR_MANIFEST = ".agents/team-kit/KIT.json"
SILENT = "[SILENT]"
NOTICE = "team-kit notice:"
# A running unit with no report this long after its launch is a gate: its session died. The same
# for checks still pending this long after a done report.
REPORT_DEADLINE_SECONDS = 24 * 3600
# A done report on a pull request with no checks at all waits this long for workflows to register
# before the unit counts as ready.
NO_CHECKS_GRACE_SECONDS = 3600
FAILED_CHECKS = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING_CHECKS = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}

Runner = Callable[..., str]


class Refused(Exception):
    pass


class Unfit(Refused):
    """A refusal about the unit itself, which only the owner can fix. tick opens a gate for it."""


class Waiting(Refused):
    """A refusal that only means "not yet": dependencies or a limit. The unit stays queued."""

    def __init__(self, message: str, scope: str):
        super().__init__(message)
        self.scope = scope  # "unit", "repo", or "machine"


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


def reason(err: Exception) -> str:
    text = str(err) if isinstance(err, (Refused, ContractError)) else f"unexpected {type(err).__name__}"
    return text[:200]


def labels_of(issue: dict) -> set[str]:
    return {str(label.get("name", "")).lower() for label in issue.get("labels") or []}


def timestamp(value: str | None) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def checks_state(rollup) -> str:
    """`passed`, `failed`, `pending`, or `none`, from a pull request's statusCheckRollup."""
    states = []
    for item in rollup or []:
        conclusion = str(item.get("conclusion") or "").upper()
        state = str(item.get("state") or "").upper()
        status = str(item.get("status") or "").upper()
        if conclusion in FAILED_CHECKS or state in FAILED_CHECKS:
            return "failed"
        states.append("pending" if (status and status != "COMPLETED") or state in PENDING_CHECKS else "passed")
    if not states:
        return "none"
    return "pending" if "pending" in states else "passed"


def in_flight(run: Runner, repo: str) -> int:
    """Units holding a slot: a coding agent or a verifier is running for each."""
    numbers: set[int] = set()
    for label in (RUNNING_LABEL, VERIFYING_LABEL):
        issues = json.loads(run(["issue", "list", "-R", repo, "--label", label, "--state", "open", "--json", "number", "--limit", "200"]))
        numbers.update(int(i["number"]) for i in issues)
    return len(numbers)


# Each project bot's GitHub login sees only its own repository, so a bot cannot count another
# project's running units. Instead every project bot writes its own count to in-flight/<bot>.json
# whenever it counts, and reads the other bots' files for the machine total. team-kit-admin status,
# which sees every repository, refreshes all of them. A launch holds a machine-wide lock from
# counting to labelling. A count older than this is ignored: no cloud session runs that long.
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
    """The bot's project: its one repository."""

    def __init__(self, policy: Policy, bot: Bot, run: Runner, now: Callable[[], float] = time.time):
        if bot.role != "project" or not bot.repo:
            raise Refused(f"{bot.name} is a {bot.role} bot; team-kit-gh is for project bots")
        self.policy, self.bot, self.run, self.now = policy, bot, run, now
        self.repo = bot.repo

    # reading ------------------------------------------------------------------------------------

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
        verifier = config.get("verifier_backend", config["backend"])
        if verifier not in VERIFIER_BACKENDS:
            raise Refused(f".agents/dispatch.json verifier_backend must be one of {', '.join(VERIFIER_BACKENDS)}")
        if verifier == "codex" and not config.get("codex_env"):
            raise Refused(".agents/dispatch.json verifier_backend codex needs codex_env, the Codex cloud environment id")
        accepted = config.get("accept_verifications", list(DEFAULT_ACCEPTED))
        if not isinstance(accepted, list) or not accepted or any(v not in VERIFICATIONS[:3] for v in accepted):
            raise Refused(f".agents/dispatch.json accept_verifications must list some of {', '.join(VERIFICATIONS[:3])}")
        return config

    def kit_check(self, local_commit: str, config: dict | None = None) -> dict:
        pinned = str((config or self.dispatch_config()).get("kit_ref", ""))
        if not SHA.match(pinned):
            raise Refused("kit_ref in .agents/dispatch.json must be a full 40-character commit")
        if local_commit != pinned:
            raise Refused(f"kit commit {local_commit[:12]} differs from kit_ref {pinned[:12]}; update one side first")
        # The cloud agent gets its skills from the copy in the repository, so that copy must be the same kit.
        try:
            vendored = json.loads(self.file(VENDOR_MANIFEST)).get("kit_ref")
        except (Refused, ValueError, AttributeError):
            raise Refused(f"{self.repo} has no vendored kit, so cloud agents would start without its skills; ask the bootstrap bot to run team-kit-admin vendor") from None
        if vendored != pinned:
            raise Refused(f"the vendored kit {str(vendored)[:12]} differs from kit_ref {pinned[:12]}; ask the bootstrap bot to run team-kit-admin vendor")
        return {"kit": local_commit, "kit_ref": pinned, "vendored": vendored}

    def depends(self, brief) -> list[tuple[str, int]]:
        refs = parse_depends(brief, self.repo)
        outside = [f"{r}#{n}" for r, n in refs if not self.bot.owns(r)]
        if outside:
            raise Refused(f"Depends on names another repository's issues: {', '.join(outside)}; a project is one repository, so list its own issues as #n")
        return refs

    def issue(self, number: int, repo: str | None = None) -> dict:
        return json.loads(self.run(["api", f"repos/{repo or self.repo}/issues/{int(number)}"]))

    def units(self, state: str = "open", search: str | None = None) -> list[dict]:
        args = ["issue", "list", "-R", self.repo, "--label", TASK_LABEL, "--state", state,
                "--json", "number,title,labels,createdAt,closedAt,stateReason", "--limit", "200"]
        if search:
            args += ["--search", search]
        return sorted(json.loads(self.run(args)), key=lambda i: int(i["number"]))

    def comments(self, issue: int) -> list[dict]:
        return json.loads(self.run(["api", "--paginate", f"repos/{self.repo}/issues/{issue}/comments"]))

    def trusted(self, comment: dict) -> bool:
        return ((comment.get("user") or {}).get("login") or "").lower() in self.policy.trusted_authors

    def last_launch(self, comments: list[dict]) -> int:
        """The index of the latest trusted launch comment, or -1."""
        found = [i for i, c in enumerate(comments) if (c.get("body") or "").startswith(LAUNCHED_MARKER) and self.trusted(c)]
        return found[-1] if found else -1

    def report_status(self, issue: int, comments: list[dict] | None = None, since_launch: bool = False) -> dict:
        """The latest trusted report. With since_launch, only one posted after the latest launch counts,
        so a relaunched unit is never judged by the report its previous agent left."""
        comments = comments if comments is not None else self.comments(issue)
        if since_launch:
            comments = comments[self.last_launch(comments) + 1:]
        reports = [c for c in comments if (c.get("body") or "").lstrip().startswith(REPORT_MARKER) and self.trusted(c)]
        if not reports:
            return {"issue": issue, "status": "none"}
        try:
            report = parse_report(reports[-1]["body"])
        except ContractError:
            return {"issue": issue, "status": "unreadable"}
        pr_url = re.compile(rf"\Ahttps://github\.com/{re.escape(self.repo)}/pull/([0-9]+)\Z", re.I)
        return {
            "issue": issue,
            "status": report.status,
            "pr": report.pr if report.pr and pr_url.match(report.pr) else None,
            "head": report.head if report.head and SHORT_SHA.match(report.head) else None,
            "at": reports[-1].get("created_at"),
        }

    def pr_state(self, pr: int) -> dict:
        data = json.loads(self.run(["pr", "view", str(pr), "-R", self.repo, "--json", "state,mergedAt,headRefOid,statusCheckRollup"]))
        head = str(data.get("headRefOid") or "")
        return {"pr": pr, "state": str(data.get("state", "")).lower(), "merged": bool(data.get("mergedAt")),
                "head": head if SHA.match(head) else None, "checks": checks_state(data.get("statusCheckRollup"))}

    def read(self, issue: int | None, pr: int | None) -> str:
        kind, number = ("pr", pr) if pr else ("issue", issue)
        return self.run([kind, "view", str(number), "-R", self.repo, "--comments"])

    # writing ------------------------------------------------------------------------------------

    def relabel(self, issue: int, add: str | None = None, remove: tuple[str, ...] = ()) -> None:
        args = ["issue", "edit", str(issue), "-R", self.repo]
        if add:
            args += ["--add-label", add]
        for name in remove:
            args += ["--remove-label", name]
        self.run(args)

    def dispatch(self, unit: str, size: str, brief_text: str, local_commit: str, cloud: Cloud) -> dict:
        """Open the unit's issue, then launch it now or leave it queued for tick."""
        if size not in UNIT_ATTEMPTS:
            raise Refused(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
        config = self.dispatch_config()
        self.kit_check(local_commit, config)
        standing = self.file(".agents/dispatch.md") if self.policy.require_brief_marker else None
        brief = parse_brief(brief_text, standing_orders=standing)
        if brief.card != unit:
            raise Refused(f"brief marker names unit {brief.card}, not {unit}")
        refs = self.depends(brief)
        body = brief_text.rstrip("\n") + "\n\n" + size_marker(size) + "\n"
        url = self.run(["issue", "create", "-R", self.repo, "--title", brief.title, "--body", body, "--label", TASK_LABEL]).strip()
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        out = {"issue": number, "url": url, "unit": unit, "depends": [f"{r}#{n}" for r, n in refs]}
        try:
            launched = self.launch(number, local_commit, cloud)
        except Waiting as wait:
            return {**out, "state": "queued", "why": str(wait)}
        except Refused as err:
            # The issue exists either way; tick retries it, so the bot never opens a second one.
            return {**out, "state": "queued", "why": f"launch failed: {err}"}
        return {**out, "state": "running", "session": launched["session"]}

    def launch(self, number: int, local_commit: str, cloud: Cloud) -> dict:
        config = self.dispatch_config()
        self.kit_check(local_commit, config)
        issue = self.launchable(number, config)
        body = issue.get("body") or ""
        try:
            brief = parse_brief(body)
            size = parse_size(body)
            refs = self.depends(brief)
        except (ContractError, Refused) as err:
            raise Unfit(str(err)) from None
        waiting, dropped = self.waiting_for(refs)
        if dropped:
            raise Unfit(f"dependency {', '.join(dropped)} was closed without merging; change the brief or reopen it")
        if waiting:
            raise Waiting(f"waiting for dependencies: {', '.join(waiting)} must be closed by a merged pull request", "unit")
        argv = launch_argv(config["backend"], self.repo, number, size, config.get("codex_env"))
        owner, name = self.repo.split("/")
        workdir = self.policy.home / "checkouts" / f"{owner}__{name}" / str(number)
        limit = int(config.get("max_in_flight", 1))
        counter = InFlight(self.policy, self.bot, self.run, self.now)
        with counter.lock():
            # Read the labels again under the lock, so a chat launch and a tick never both start it.
            labels = labels_of(self.launchable(number, config))
            counts = counter.count_own()
            if counts[self.repo] >= limit:
                raise Waiting(f"repo limit: {limit} unit(s) already running in {self.repo} (max_in_flight)", "repo")
            total = sum(counts.values()) + counter.others()
            if total >= self.policy.max_in_flight_total:
                raise Waiting(f"machine limit: {total} unit(s) already running on this machine (max_in_flight_total)", "machine")
            self.relabel(number, add=RUNNING_LABEL, remove=(HUMAN_LABEL,) if HUMAN_LABEL in labels else ())
            counter.write({**counts, self.repo: counts[self.repo] + 1})
        try:
            session = cloud.start(config["backend"], self.repo, argv, workdir)
        except Exception as err:
            # Any failure, a missing `claude` binary included, must free the unit again.
            self.relabel(number, remove=(RUNNING_LABEL,))
            counter.write(counts)
            reason = str(err) if isinstance(err, (CloudError, Refused)) else type(err).__name__ + (f": {err.filename}" if getattr(err, "filename", None) else "")
            raise Refused(f"launch failed: {reason}") from err
        try:
            self.run(["issue", "comment", str(number), "-R", self.repo, "--body", f"{LAUNCHED_MARKER}\nsession: {session}\nbackend: {config['backend']}\n"])
        except Exception:  # noqa: BLE001 - the agent is running; a missing comment must not undo that
            pass
        return {"issue": number, "unit": brief.card, "backend": config["backend"], "session": session}

    def launchable(self, number: int, config: dict) -> dict:
        issue = self.issue(number)
        labels = labels_of(issue)
        author = str((issue.get("user") or {}).get("login") or "").lower()
        if "pull_request" in issue or issue.get("state") != "open" or TASK_LABEL not in labels:
            raise Unfit(f"#{number} is not an open {TASK_LABEL} issue")
        if RUNNING_LABEL in labels or VERIFYING_LABEL in labels:
            raise Refused(f"#{number} is already launched ({RUNNING_LABEL} or {VERIFYING_LABEL}); never launch a unit twice")
        if REVIEW_LABEL in labels:
            raise Refused(f"#{number} is done and waits for the owner to merge ({REVIEW_LABEL})")
        if author not in {str(d).lower() for d in config.get("trusted_dispatchers") or []}:
            raise Unfit(f"#{number} was written by {author or 'nobody'}, who is not in trusted_dispatchers")
        return issue

    def waiting_for(self, refs: list[tuple[str, int]]) -> tuple[list[str], list[str]]:
        """Dependencies still open, and those closed without completing."""
        waiting, dropped = [], []
        for repo, number in refs:
            dep = self.issue(number, repo)
            if dep.get("state") != "closed":
                waiting.append(f"{repo}#{number}")
            elif dep.get("state_reason") not in (None, "completed"):
                dropped.append(f"{repo}#{number}")
        return waiting, dropped

    def gate(self, issue: dict, why: str) -> str:
        """The agent stopped and needs the owner. Frees the unit's slot."""
        held = tuple(label for label in (RUNNING_LABEL, VERIFYING_LABEL) if label in labels_of(issue))
        self.relabel(int(issue["number"]), add=HUMAN_LABEL, remove=held)
        return f"{NOTICE} #{issue['number']} needs you: {why}. https://github.com/{self.repo}/issues/{issue['number']}"

    def check_running(self, issue: dict, cloud: Cloud | None = None) -> str | None:
        """One running unit: free its slot when its agent has finished. Returns a notice, or None."""
        number = int(issue["number"])
        comments = self.comments(number)
        report = self.report_status(number, comments, since_launch=True)
        status = report["status"]
        if status == "none":
            launch = self.last_launch(comments)
            started = timestamp(comments[launch].get("created_at")) if launch >= 0 else timestamp(issue.get("createdAt"))
            if started is not None and self.now() - started > REPORT_DEADLINE_SECONDS:
                return self.gate(issue, "no report 24 hours after launch; check its session")
            return None
        if status != "done":
            return self.gate(issue, f"the agent reported {status}")
        if not report["pr"]:
            return self.gate(issue, "the agent reported done without a pull request; open it from its session")
        pr = self.pr_state(int(report["pr"].rsplit("/", 1)[1]))
        reported = timestamp(report.get("at")) or self.now()
        if pr["merged"]:
            return self.gate(issue, f"{report['pr']} merged but the issue is still open; close it as completed")
        if pr["state"] == "closed":
            return self.gate(issue, f"{report['pr']} was closed without merging")
        if pr["checks"] == "failed":
            return self.gate(issue, f"checks failed on {report['pr']} after a done report")
        waiting = pr["checks"] == "pending" or (pr["checks"] == "none" and self.now() - reported < NO_CHECKS_GRACE_SECONDS)
        if waiting:
            if self.now() - reported > REPORT_DEADLINE_SECONDS:
                return self.gate(issue, f"checks on {report['pr']} are still pending a day after a done report")
            return None
        checks = "checks passed" if pr["checks"] == "passed" else "no checks ran"
        config = self.dispatch_config()
        verifier = config.get("verifier_backend", config["backend"])
        if verifier == "none" or cloud is None:
            self.relabel(number, add=REVIEW_LABEL, remove=(RUNNING_LABEL,))
            return f"{NOTICE} #{number} is ready for you to merge ({checks}, not independently verified): {report['pr']}"
        return self.start_verifier(issue, report["pr"], pr["head"], verifier, config, cloud, pr["checks"])

    # Verification -------------------------------------------------------------------------------
    # Each verifier launch is recorded under policy.home/verify/<bot>/<issue>.json: the commit it
    # verifies, its pull request, when it started, and the key that signs its verdict. The key is
    # never posted, so a verdict counts only from the session tick started.

    def verify_record_path(self, number: int) -> Path:
        return self.policy.home / "verify" / self.bot.name / f"{int(number)}.json"

    def verify_record(self, number: int) -> dict | None:
        try:
            record = json.loads(self.verify_record_path(number).read_text())
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) and SHA.match(str(record.get("head", ""))) else None

    def drop_verify_record(self, number: int) -> None:
        self.verify_record_path(number).unlink(missing_ok=True)

    def start_verifier(self, issue: dict, pr_url: str, head: str | None, backend: str, config: dict, cloud: Cloud, checks: str = "passed") -> str | None:
        """Hand the unit's slot from its coding agent to a verifier session that did not write the change."""
        number = int(issue["number"])
        key = secrets.token_hex(16)
        try:
            argv = verify_argv(backend, self.repo, number, pr_url, head or "", key, config.get("codex_env"))
        except ContractError as err:
            return self.gate(issue, f"the verifier could not start: {err}")
        path = self.verify_record_path(number)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps({"pr": pr_url, "head": head, "key": key, "backend": backend, "started": self.now(), "checks": checks}))
        tmp.replace(path)
        owner, name = self.repo.split("/")
        workdir = self.policy.home / "checkouts" / f"{owner}__{name}" / f"{number}-verify"
        held = tuple(label for label in (RUNNING_LABEL, HUMAN_LABEL, REVIEW_LABEL) if label in labels_of(issue))
        self.relabel(number, add=VERIFYING_LABEL, remove=held)
        try:
            session = cloud.start(backend, self.repo, argv, workdir)
        except Exception as err:  # noqa: BLE001 - any failure leaves the unit to the owner
            self.drop_verify_record(number)
            issue = {**issue, "labels": [{"name": VERIFYING_LABEL}]}
            why = str(err) if isinstance(err, (CloudError, Refused)) else type(err).__name__
            return self.gate(issue, f"the verifier could not start: {why}")
        try:
            self.run(["issue", "comment", str(number), "-R", self.repo, "--body",
                      f"{VERIFY_LAUNCHED_MARKER}\nsession: {session}\nbackend: {backend}\npr: {pr_url}\nhead: {head}\n"])
        except Exception:  # noqa: BLE001 - the verifier is running and tick tracks it from its record
            pass
        return None

    def check_verifying(self, issue: dict) -> str | None:
        """One unit under verification: move it on when its verdict arrives. Returns a notice, or None."""
        number = int(issue["number"])
        record = self.verify_record(number)
        if record is None:
            return self.gate(issue, f"this machine has no record of its verifier; run `team-kit-gh verify --issue {number}` to start one")
        comments = self.comments(number)
        # Only comments after the latest launch of either kind can answer this verifier.
        starts = [i for i, c in enumerate(comments) if (c.get("body") or "").startswith((LAUNCHED_MARKER, VERIFY_LAUNCHED_MARKER)) and self.trusted(c)]
        verdict = None
        for comment in comments[(starts[-1] + 1) if starts else 0:]:
            body = (comment.get("body") or "").lstrip()
            if not body.startswith(VERDICT_MARKER) or not self.trusted(comment):
                continue
            if comment.get("updated_at") and comment.get("updated_at") != comment.get("created_at"):
                continue  # an edited verdict never counts
            try:
                candidate = parse_verdict(body)
            except ContractError:
                continue
            if verdict_signed(candidate, record["key"], record["head"]):
                verdict = candidate
                break  # the first signed verdict is the verifier's answer
        if verdict is None:
            if self.now() - float(record.get("started", 0)) > REPORT_DEADLINE_SECONDS:
                self.drop_verify_record(number)
                return self.gate(issue, "no signed verdict 24 hours after the verifier started; check its session")
            return None
        accepted = self.dispatch_config().get("accept_verifications", list(DEFAULT_ACCEPTED))
        if verdict.verification not in accepted:
            self.drop_verify_record(number)
            return self.gate(issue, f"the verifier reported {verdict.verification}; read its verdict on the issue")
        pr_url = record["pr"]
        pr = self.pr_state(int(pr_url.rsplit("/", 1)[1]))
        if pr["merged"] or pr["state"] == "closed":
            self.drop_verify_record(number)
            return self.gate(issue, f"{pr_url} was {'merged' if pr['merged'] else 'closed'} during verification")
        if pr["head"] != record["head"]:
            self.drop_verify_record(number)
            return self.gate(issue, f"{pr_url} changed during verification; run `team-kit-gh verify --issue {number}` to verify its new head")
        if pr["checks"] == "failed":
            self.drop_verify_record(number)
            return self.gate(issue, f"checks failed on {pr_url} during verification")
        if pr["checks"] == "pending":
            return None
        self.drop_verify_record(number)
        self.relabel(number, add=REVIEW_LABEL, remove=(VERIFYING_LABEL,))
        checks = "checks passed" if pr["checks"] == "passed" else "no checks ran"
        return f"{NOTICE} #{number} is ready for you to merge ({checks}, verified: {verdict.verification}): {pr_url}"

    def verify(self, number: int, local_commit: str, cloud: Cloud) -> dict:
        """Start a new verifier on a unit's current pull request head, without redoing the coding work."""
        config = self.dispatch_config()
        self.kit_check(local_commit, config)
        issue = self.issue(number)
        labels = labels_of(issue)
        if issue.get("state") != "open" or TASK_LABEL not in labels:
            raise Refused(f"#{number} is not an open {TASK_LABEL} issue")
        if labels & {RUNNING_LABEL, VERIFYING_LABEL}:
            raise Refused(f"#{number} is already running or verifying; wait for it")
        backend = config.get("verifier_backend", config["backend"])
        if backend == "none":
            raise Refused("verifier_backend is none in .agents/dispatch.json")
        report = self.report_status(number, since_launch=True)
        if report["status"] != "done" or not report.get("pr"):
            raise Refused(f"#{number} has no done report with a pull request to verify")
        pr = self.pr_state(int(report["pr"].rsplit("/", 1)[1]))
        if pr["merged"] or pr["state"] == "closed" or not pr["head"]:
            raise Refused(f"{report['pr']} is not open")
        if pr["checks"] in ("failed", "pending"):
            raise Refused(f"checks on {report['pr']} are {pr['checks']}; verify once they pass")
        counter = InFlight(self.policy, self.bot, self.run, self.now)
        with counter.lock():
            counts = counter.count_own()
            limit = int(config.get("max_in_flight", 1))
            if counts[self.repo] >= limit:
                raise Waiting(f"repo limit: {limit} unit(s) already running in {self.repo} (max_in_flight)", "repo")
            if sum(counts.values()) + counter.others() >= self.policy.max_in_flight_total:
                raise Waiting("machine limit: max_in_flight_total is reached", "machine")
            notice = self.start_verifier({**issue, "labels": [{"name": n} for n in labels]}, report["pr"], pr["head"], backend, config, cloud, pr["checks"])
            counter.count_own()
        if notice:
            raise Refused(notice.removeprefix(f"{NOTICE} "))
        return {"issue": number, "pr": report["pr"], "head": pr["head"], "verifier": backend}


class Project:
    """tick and status for the bot's project."""

    def __init__(self, policy: Policy, bot: Bot, run: Runner, now: Callable[[], float] = time.time):
        self.policy, self.bot, self.run, self.now = policy, bot, run, now
        self.gh = Gh(policy, bot, run, now)

    def tick(self, local_commit: str, cloud: Cloud) -> list[str]:
        gh, notices, problems = self.gh, [], {}
        queued: list[dict] = []
        try:
            units = gh.units()
        except Exception as err:  # noqa: BLE001 - reported once, retried next tick
            units, problems[gh.repo] = [], reason(err)
        for issue in units:
            labels = labels_of(issue)
            if RUNNING_LABEL in labels or VERIFYING_LABEL in labels:
                try:
                    notice = gh.check_verifying(issue) if VERIFYING_LABEL in labels else gh.check_running(issue, cloud)
                except Exception as err:  # noqa: BLE001 - one broken unit never stops the others
                    problems[f"{gh.repo}#{issue['number']}"] = reason(err)
                    continue
                if notice:
                    notices.append(notice)
            elif not labels & {REVIEW_LABEL, HUMAN_LABEL}:
                queued.append(issue)
        queued.sort(key=lambda issue: (issue.get("createdAt") or "", int(issue["number"])))
        for issue in queued:
            try:
                gh.launch(int(issue["number"]), local_commit, cloud)
            except Waiting as wait:
                if wait.scope != "unit":
                    break  # the repository or the machine is full
            except Unfit as err:
                notices.append(gh.gate(issue, f"it could not launch: {err}"))
            except Exception as err:  # noqa: BLE001 - a pin mismatch or a GitHub hiccup is not the unit's fault
                problems[gh.repo] = reason(err)
                break
        try:
            # Refresh this bot's count for the other bots, whatever launched or did not.
            InFlight(self.policy, self.bot, self.run, self.now).count_own()
        except Exception as err:  # noqa: BLE001
            problems["count"] = reason(err)
        return notices + self.new_problems(problems)

    def new_problems(self, problems: dict[str, str]) -> list[str]:
        """A notice for each problem tick has not reported yet, so a lasting one is said once."""
        path = self.policy.home / "tick" / f"{self.bot.name}.json"
        try:
            seen = json.loads(path.read_text())
        except (OSError, ValueError):
            seen = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(problems))
        return [f"{NOTICE} tick could not handle {where}: {why}" for where, why in sorted(problems.items()) if seen.get(where) != why]

    def status(self) -> list[str]:
        marker = self.policy.home / "status" / f"{self.bot.name}.json"
        try:
            since = float(json.loads(marker.read_text())["at"])
        except (OSError, ValueError, KeyError, TypeError):
            since = self.now() - 24 * 3600
        since_iso = dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flight, waiting, done = [], [], []
        for issue in self.gh.units():
            labels, ref = labels_of(issue), f"#{issue['number']} {issue['title']}"
            if RUNNING_LABEL in labels:
                flight.append(f"{ref} (running)")
            elif VERIFYING_LABEL in labels:
                flight.append(f"{ref} (verifying)")
            elif HUMAN_LABEL in labels:
                waiting.append(f"{ref} (gate)")
            elif REVIEW_LABEL in labels:
                waiting.append(f"{ref} (merge)")
            else:
                flight.append(f"{ref} (queued)")
        for issue in self.gh.units("closed", search=f"closed:>={since_iso}"):
            if (timestamp(issue.get("closedAt")) or 0) >= since and str(issue.get("stateReason") or "").upper() in ("", "COMPLETED"):
                done.append(f"#{issue['number']} {issue['title']}")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"at": self.now()}))
        if not (flight or waiting or done):
            return [SILENT]
        line = lambda name, items: f"{NOTICE} {name}: {len(items)}. " + ("; ".join(items) if items else "none") + "."  # noqa: E731
        return [line("In flight", flight), line("Waiting on you", waiting), line("Done since last", done)]


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
    for name in ("project", "tick", "status"):
        sub.add_parser(name)
    for name in ("dispatch", "launch", "verify", "report-status", "read", "show", "pr-state", "kit-check"):
        cmd = sub.add_parser(name)
        if name == "dispatch":
            cmd.add_argument("--unit", required=True)
            cmd.add_argument("--size", required=True, choices=list(UNIT_ATTEMPTS))
            cmd.add_argument("--brief-file", required=True)
        elif name == "read":
            cmd.add_argument("--issue", type=int)
            cmd.add_argument("--pr", type=int)
        elif name == "show":
            cmd.add_argument("--path", required=True)
        elif name == "pr-state":
            cmd.add_argument("--pr", type=int, required=True)
        elif name in ("launch", "verify", "report-status"):
            cmd.add_argument("--issue", type=int, required=True)
    return top


def main(argv: list[str] | None = None, run: Runner = run_gh, bot: str | None = None, cloud: Cloud | None = None,
         commit: Callable[[Policy], str] = kit_commit, now: Callable[[], float] = time.time) -> int:
    args = parser().parse_args(argv)
    try:
        policy = load_policy()
        me = policy.bot(bot or current_bot())
        if me.role != "project":
            raise Refused(f"{me.name} is a {me.role} bot; team-kit-gh is for project bots")
        cloud = cloud or Cloud(run_gh=run)
        if args.command == "project":
            out: object = {"bot": me.name, "project": me.project, "repo": me.repo}
        elif args.command in ("tick", "status"):
            project = Project(policy, me, run, now)
            lines = project.tick(commit(policy), cloud) if args.command == "tick" else project.status()
            print("\n".join(lines) if lines else SILENT)
            return 0
        else:
            gh = Gh(policy, me, run, now)
            if args.command == "dispatch":
                out = gh.dispatch(args.unit, args.size, brief_text(policy, me, args.brief_file), commit(policy), cloud)
            elif args.command == "launch":
                out = gh.launch(args.issue, commit(policy), cloud)
            elif args.command == "verify":
                out = gh.verify(args.issue, commit(policy), cloud)
            elif args.command == "report-status":
                out = {k: v for k, v in gh.report_status(args.issue).items() if k != "at"}
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
