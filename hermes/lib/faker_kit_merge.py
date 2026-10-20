from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import json
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

WINDOW_SECONDS = 3600
MERGE_METHOD = "squash"
REVIEWER = "claude[bot]"
SECURITY_CHECK = "security-review"
SECURITY_JOB = "security"
REVIEW_WORKFLOW = ".github/workflows/claude-review.yml"
STICKY_FAILURES = ("failure", "timed_out")
OWNER_ONLY_PATHS = (
    ".github/workflows/",
    ".agents/dispatch.json",
    "hermes/plugin/faker_kit_guard/",
    "hermes/templates/guard.yaml",
    ".agents/faker-kit/hermes/plugin/faker_kit_guard/",
    ".agents/faker-kit/hermes/templates/guard.yaml",
)
GLOB_CHARS = "*?["
MAX_LISTED_FILES = 3000
PASSING_CONCLUSIONS = ("success", "neutral", "skipped")
FORMAL_REVIEWS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
ACCEPTED_RISK = ("low", "medium")
RISK_LINE = re.compile(r"^[ \t]*Risk:[ \t]*(low|medium|high)[ \t]*$", re.I | re.M)
NOTICE = "faker-kit notice:"
TOKEN_CHARS = 40


@dataclass(frozen=True)
class Window:
    head: str
    at: float


@dataclass(frozen=True)
class Evidence:
    check_runs: list[dict]
    statuses: list[dict]
    reviews: list[dict]
    files: list[dict]
    rules: list[str]
    security_run: dict | None = field(default=None)
    security_jobs: list[dict] | None = field(default=None)
    head_pulls: list[dict] | None = field(default=None)
    head_jobs: list[dict] | None = field(default=None)


@dataclass(frozen=True)
class Verdict:
    action: str
    message: str
    window: Window | None = None
    notice: str | None = None


def from_fork(pull: dict, repo: str) -> bool:
    return str(((pull.get("head") or {}).get("repo") or {}).get("full_name") or "").lower() != repo.lower()


def token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_./\[\]-]", "?", text)[:TOKEN_CHARS]


def login(review: dict) -> str:
    return str((review.get("user") or {}).get("login") or "").lower()


def is_bot(review: dict) -> bool:
    return (review.get("user") or {}).get("type") == "Bot"


def checks_problem(check_runs: list[dict], statuses: list[dict]) -> str | None:
    failed = pending = 0
    statuses = [s for s in statuses if s.get("context") != SECURITY_CHECK]
    for run in check_runs:
        if run.get("status") != "completed":
            pending += 1
        elif str(run.get("conclusion") or "").lower() not in PASSING_CONCLUSIONS:
            failed += 1
    for status in statuses:
        state = str(status.get("state") or "").lower()
        pending += state == "pending"
        failed += state not in ("success", "pending")
    if not check_runs and not statuses:
        return "GitHub reports no checks on the head"
    if failed or pending:
        return f"checks have not all passed ({failed} failed, {pending} pending)"
    return None


def security_status(statuses: list[dict]) -> dict | None:
    return next((s for s in statuses if s.get("context") == SECURITY_CHECK), None)


def run_url(repo: str) -> re.Pattern[str]:
    return re.compile(rf"\Ahttps://github\.com/{re.escape(repo)}/actions/runs/([0-9]+)(?:[/?#].*)?\Z", re.I)


def security_run_id(statuses: list[dict], repo: str) -> int | None:
    status = security_status(statuses)
    found = run_url(repo).match(str((status or {}).get("target_url") or ""))
    return int(found.group(1)) if found else None


def security_problem(statuses: list[dict], run: dict | None, jobs: list[dict] | None, head: str, repo: str, pr: int, default: str) -> str | None:
    status = security_status(statuses)
    if status is None:
        return f"{SECURITY_CHECK} has not reported on the head"
    if str(status.get("state") or "").lower() != "success":
        return f"{SECURITY_CHECK} did not succeed on the head"
    run_id = security_run_id(statuses, repo)
    if run_id is None:
        return f"the {SECURITY_CHECK} status does not point at a run of this repository's review workflow"
    workflow = str((run or {}).get("path") or "").split("@")[0]
    run_repo = str(((run or {}).get("repository") or {}).get("full_name") or "").lower()
    if run is None or run.get("id") != run_id or workflow != REVIEW_WORKFLOW or run.get("head_sha") != head or run.get("event") != "pull_request" or run_repo != repo.lower():
        return f"the {SECURITY_CHECK} status does not come from a {REVIEW_WORKFLOW} run on this head"
    attached = [p for p in run.get("pull_requests") or [] if isinstance(p, dict)]
    if not any(p.get("number") == pr and (p.get("base") or {}).get("ref") == default for p in attached):
        return f"the {SECURITY_CHECK} run was not a run for #{pr} against {default}"
    found = [j for j in jobs or [] if j.get("name") == SECURITY_JOB]
    if len(found) != 1 or found[0].get("run_id") != run_id or found[0].get("head_sha") != head:
        return f"run {run_id} of {REVIEW_WORKFLOW} has no single {SECURITY_JOB} job on this head"
    if found[0].get("status") != "completed":
        return f"the {SECURITY_JOB} job of run {run_id} has not finished"
    if found[0].get("conclusion") != "success":
        return f"the {SECURITY_JOB} job of run {run_id} did not succeed"
    return None


def head_problem(pulls: list[dict] | None, jobs: list[dict] | None, pr: int, head: str) -> str | None:
    if pulls is None or jobs is None:
        return "the pull requests and security runs on this head could not be read"
    others = sorted(int(p["number"]) for p in pulls
                    if isinstance(p.get("number"), int) and p["number"] != pr and p.get("state") == "open" and (p.get("head") or {}).get("sha") == head)
    if others:
        return f"another open pull request (#{others[0]}) has the same head, so its runs could stand in for #{pr}'s; close it or push a new commit"
    if any(j.get("status") == "completed" and j.get("conclusion") in STICKY_FAILURES for j in jobs):
        return f"a {SECURITY_JOB} job failed on this head, and a failed verdict sticks; push a new commit"
    return None


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def owner_path_match(path: str, rule: str) -> bool:
    path, rule = fold(path), fold(rule)
    name = path.rsplit("/", 1)[-1]
    if rule.endswith("/"):
        return ("/" + rule) in ("/" + path)
    if any(char in rule for char in GLOB_CHARS):
        return fnmatch.fnmatchcase(path, rule) or fnmatch.fnmatchcase(name, rule)
    return path == rule or path.startswith(rule + "/") or name == rule


def owner_path_problem(files: list[dict], rules: list[str]) -> str | None:
    if len(files) >= MAX_LISTED_FILES:
        return f"the change lists {MAX_LISTED_FILES} files or more, more than GitHub shows; the owner merges it"
    for entry in files:
        for path in (str(entry.get("filename") or ""), str(entry.get("previous_filename") or "")):
            hit = next((rule for rule in (*OWNER_ONLY_PATHS, *rules) if path and owner_path_match(path, rule)), None)
            if hit:
                return f"the change touches {token(path)}, which {token(hit)} protects; the owner merges it"
    return None


def dispatch_rules(text: str | None) -> list[str]:
    if text is None:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError(".agents/dispatch.json on the default branch is not valid JSON") from None
    rules: list[str] = []
    for key in ("risk_paths_high", "forbidden_paths"):
        value = data.get(key) if isinstance(data, dict) else None
        if value is not None and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
            raise ValueError(f".agents/dispatch.json {key} must be a list of paths")
        rules += [item for item in value or [] if item]
    return rules


def review_problem(reviews: list[dict], head: str) -> str | None:
    formal = [r for r in reviews if login(r) == REVIEWER and is_bot(r) and r.get("state") in FORMAL_REVIEWS]
    if not formal:
        return f"{REVIEWER} has not reviewed this pull request"
    latest = formal[-1]
    if latest.get("commit_id") != head:
        return f"{REVIEWER}'s latest review is of an older commit, not the current head"
    if latest.get("state") != "APPROVED":
        return f"{REVIEWER}'s latest review on the head is not an approval"
    risks = RISK_LINE.findall(str(latest.get("body") or ""))
    if not risks:
        return f"{REVIEWER}'s approval has no Risk line"
    if risks[-1].lower() not in ACCEPTED_RISK:
        return f"{REVIEWER} rates the change Risk: {risks[-1].lower()}; the owner merges it"
    return None


def requested_changes(reviews: list[dict]) -> int:
    latest: dict[str, str] = {}
    for review in reviews:
        if not is_bot(review) and review.get("state") in FORMAL_REVIEWS:
            latest[login(review)] = str(review.get("state"))
    return sum(state == "CHANGES_REQUESTED" for state in latest.values())


def decide(pr: int, head: str, evidence: Evidence, repo: str, default: str, window: Window | None, now: float) -> Verdict:
    problem = (owner_path_problem(evidence.files, evidence.rules) or checks_problem(evidence.check_runs, evidence.statuses)
               or review_problem(evidence.reviews, head) or security_problem(evidence.statuses, evidence.security_run, evidence.security_jobs, head, repo, pr, default)
               or head_problem(evidence.head_pulls, evidence.head_jobs, pr, head))
    if problem:
        return Verdict("refuse", problem)
    if requested_changes(evidence.reviews):
        return Verdict("refuse", "a reviewer requested changes; the author or the reviewer must resolve it")
    if window is None or window.head != head:
        fresh = Window(head, now)
        return Verdict("wait", f"every gate passed; the {WINDOW_SECONDS // 60}-minute window for #{pr} has not started; faker-kit merge --pr {pr} starts it", fresh,
                       f"{NOTICE} PR #{pr} merges in {WINDOW_SECONDS // 3600} hour unless you request changes")
    left = window.at + WINDOW_SECONDS - now
    if left > 0:
        return Verdict("wait", f"#{pr}'s window has {int(left // 60) + 1} minutes left")
    return Verdict("merge", "every gate passed and the window has run")


class WindowStore:
    def __init__(self, home: Path, bot: str) -> None:
        self.path = home / "merge" / f"{bot}.json"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_name(f"{self.path.name}.lock").open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, pr: int) -> Window | None:
        row = self.read().get(str(pr))
        if isinstance(row, dict) and isinstance(row.get("head"), str) and isinstance(row.get("at"), (int, float)):
            return Window(row["head"], float(row["at"]))
        return None

    def put(self, pr: int, window: Window | None) -> None:
        data = self.read()
        if window is None:
            data.pop(str(pr), None)
        else:
            data[str(pr)] = {"head": window.head, "at": window.at}
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}")
        tmp.touch(mode=0o600)
        tmp.write_text(json.dumps(data))
        tmp.replace(self.path)
