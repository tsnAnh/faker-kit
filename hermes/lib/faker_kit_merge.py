from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MERGE_METHOD = "squash"
REVIEWER = "claude[bot]"
SECURITY_CHECK = "security-review"
SECURITY_JOB = "security"
REVIEW_WORKFLOW = ".github/workflows/claude-review.yml"
KIT_CHECKS_WORKFLOW = ".github/workflows/faker-kit-checks.yml"
NOT_CI_WORKFLOWS = (REVIEW_WORKFLOW, KIT_CHECKS_WORKFLOW)
WORKFLOW_FOLDER = ".github/workflows/"
BASE_CHANGE_EVENTS = ("base_ref_changed", "automatic_base_change_succeeded", "automatic_base_change_failed")
STICKY_FAILURES = ("failure", "timed_out")
OWNER_ONLY_PATHS = (
    ".github/workflows/",
    ".agents/dispatch.json",
    "hermes/plugin/faker_kit_guard/",
    "hermes/templates/guard.yaml",
    ".agents/faker-kit/",
)
GLOB_CHARS = "*?["
MAX_LISTED_FILES = 3000
PASSING_CONCLUSIONS = ("success", "neutral", "skipped")
FORMAL_REVIEWS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
ACCEPTED_RISK = ("low", "medium")
RISK_LINE = re.compile(r"^[ \t]*Risk:[ \t]*(low|medium|high)[ \t]*$", re.I | re.M)
TOKEN_CHARS = 40
GATE_PATHS = "protected-paths"
GATE_APPROVAL = "claude-approval"
GATE_RISK = "risk-high"


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
    head_runs: list[dict] | None = field(default=None)
    head_ref: str = ""
    branch_pulls: list[dict] | None = field(default=None)
    timeline: list[dict] | None = field(default=None)
    opened: str = ""
    workflows_match: bool = False


@dataclass(frozen=True)
class Verdict:
    action: str
    message: str
    bypassed: tuple[str, ...] = ()


def from_fork(pull: dict, repo: str) -> bool:
    return str(((pull.get("head") or {}).get("repo") or {}).get("full_name") or "").lower() != repo.lower()


def token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_./\[\]-]", "?", text)[:TOKEN_CHARS]


def login(review: dict) -> str:
    return str((review.get("user") or {}).get("login") or "").lower()


def is_bot(review: dict) -> bool:
    return (review.get("user") or {}).get("type") == "Bot"


def workflow_path(run: dict) -> str:
    return str(run.get("path") or "").split("@")[0]


def instant(text: object) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def from_repo(run: dict, repo: str) -> bool:
    return str((run.get("head_repository") or {}).get("full_name") or "").lower() == repo.lower()


def run_for_pull(run: dict, pr: int, default: str, ref: str, repo: str, opened: str) -> bool:
    attached = run.get("pull_requests")
    pulls = attached if isinstance(attached, list) else []
    pinned = any(isinstance(p, dict) and p.get("number") == pr and (p.get("base") or {}).get("ref") == default for p in pulls)
    started, created = instant(run.get("created_at")), instant(opened)
    return (bool(ref) and run.get("head_branch") == ref and pinned and from_repo(run, repo)
            and started is not None and created is not None and started >= created)


def ci_run_counts(run: dict, head: str, repo: str, pr: int, default: str, ref: str, opened: str, workflows_match: bool) -> bool:
    event = run.get("event")
    return (isinstance(run.get("check_suite_id"), int) and run.get("head_sha") == head
            and str((run.get("repository") or {}).get("full_name") or "").lower() == repo.lower()
            and workflow_path(run).startswith(WORKFLOW_FOLDER) and workflow_path(run) not in NOT_CI_WORKFLOWS
            and from_repo(run, repo)
            and ((event == "push" and workflows_match) or (event == "pull_request" and run_for_pull(run, pr, default, ref, repo, opened))))


def ci_suites(head_runs: list[dict], head: str, repo: str, pr: int, default: str, ref: str, opened: str, workflows_match: bool) -> set[int]:
    return {run["check_suite_id"] for run in head_runs if ci_run_counts(run, head, repo, pr, default, ref, opened, workflows_match)}


def ci_missing(check_runs: list[dict], head_runs: list[dict] | None, head: str, repo: str, pr: int, default: str, ref: str, opened: str, workflows_match: bool) -> str | None:
    if head_runs is None:
        return "the workflow runs on this head could not be read"
    suites = ci_suites(head_runs, head, repo, pr, default, ref, opened, workflows_match)
    if any((run.get("check_suite") or {}).get("id") in suites and run.get("status") == "completed"
           and str(run.get("conclusion") or "").lower() == "success" for run in check_runs):
        return None
    hint = "" if workflows_match else f"; the branch's workflows differ from {default}; rebase onto {default}"
    return (f"no check run of a push or pull_request run of a CI workflow from this branch of {repo}, started after #{pr} opened against {default}, succeeded on the head "
            f"({REVIEW_WORKFLOW} and {KIT_CHECKS_WORKFLOW} do not count); "
            "the repository needs GitHub Actions CI that builds and tests every pull request" + hint)


def checks_problem(check_runs: list[dict], statuses: list[dict], head_runs: list[dict] | None, head: str, repo: str, pr: int, default: str, ref: str, opened: str, workflows_match: bool,
                   owner_approved: bool = False) -> str | None:
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
        return "GitHub reports no checks on the head; the repository needs GitHub Actions CI that builds and tests every pull request"
    if owner_approved and pending and not failed:
        return "checks still running; the owner asks again when they finish"
    if failed or pending:
        return f"checks have not all passed ({failed} failed, {pending} pending)"
    return ci_missing(check_runs, head_runs, head, repo, pr, default, ref, opened, workflows_match)


def security_status(statuses: list[dict]) -> dict | None:
    return next((s for s in statuses if s.get("context") == SECURITY_CHECK), None)


def run_url(repo: str) -> re.Pattern[str]:
    return re.compile(rf"\Ahttps://github\.com/{re.escape(repo)}/actions/runs/([0-9]+)(?:[/?#].*)?\Z", re.I)


def security_run_id(statuses: list[dict], repo: str) -> int | None:
    status = security_status(statuses)
    found = run_url(repo).match(str((status or {}).get("target_url") or ""))
    return int(found.group(1)) if found else None


def security_problem(statuses: list[dict], run: dict | None, jobs: list[dict] | None, head: str, repo: str, pr: int, default: str, ref: str, opened: str) -> str | None:
    status = security_status(statuses)
    if status is None:
        return f"{SECURITY_CHECK} has not reported on the head"
    if str(status.get("state") or "").lower() != "success":
        return f"{SECURITY_CHECK} did not succeed on the head"
    run_id = security_run_id(statuses, repo)
    if run_id is None:
        return f"the {SECURITY_CHECK} status does not point at a run of this repository's review workflow"
    workflow = workflow_path(run or {})
    run_repo = str(((run or {}).get("repository") or {}).get("full_name") or "").lower()
    if run is None or run.get("id") != run_id or workflow != REVIEW_WORKFLOW or run.get("head_sha") != head or run.get("event") != "pull_request" or run_repo != repo.lower():
        return f"the {SECURITY_CHECK} status does not come from a {REVIEW_WORKFLOW} run on this head"
    if not run_for_pull(run, pr, default, ref, repo, opened):
        return f"the {SECURITY_CHECK} run was not a run for #{pr} from {token(ref)} in {repo} against {default} that started after the pull request opened"
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


def base_problem(pulls: list[dict] | None, timeline: list[dict] | None, pr: int, default: str) -> str | None:
    if pulls is None or timeline is None:
        return "the pull requests from this branch and this pull request's timeline could not be read"
    if not any(p.get("number") == pr and (p.get("base") or {}).get("ref") == default for p in pulls):
        return f"the pull requests from this branch do not include #{pr} against {default}, so the branch query did not match"
    for pull in sorted(pulls, key=lambda p: int(p["number"]) if isinstance(p.get("number"), int) else 0):
        base = str((pull.get("base") or {}).get("ref") or "")
        if base != default:
            return f"pull request #{pull.get('number')} from this branch targets {token(base)}, not {default}; a run from it could carry another base's workflows; the owner merges it"
    if any(event.get("event") in BASE_CHANGE_EVENTS for event in timeline):
        return f"the base of #{pr} was changed after it was opened, so earlier runs may have used another base's workflows; open a fresh pull request"
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


def review_issue(reviews: list[dict], head: str) -> tuple[str, str] | None:
    formal = [r for r in reviews if login(r) == REVIEWER and is_bot(r) and r.get("state") in FORMAL_REVIEWS]
    if not formal:
        return GATE_APPROVAL, f"{REVIEWER} has not reviewed this pull request"
    latest = formal[-1]
    if latest.get("commit_id") != head:
        return GATE_APPROVAL, f"{REVIEWER}'s latest review is of an older commit, not the current head"
    if latest.get("state") != "APPROVED":
        return GATE_APPROVAL, f"{REVIEWER}'s latest review on the head is not an approval"
    risks = RISK_LINE.findall(str(latest.get("body") or ""))
    if not risks:
        return GATE_APPROVAL, f"{REVIEWER}'s approval has no Risk line"
    if risks[-1].lower() not in ACCEPTED_RISK:
        return GATE_RISK, f"{REVIEWER} rates the change Risk: {risks[-1].lower()}; the owner merges it"
    return None


def review_problem(reviews: list[dict], head: str) -> str | None:
    issue = review_issue(reviews, head)
    return issue[1] if issue else None


def requested_changes(reviews: list[dict]) -> int:
    latest: dict[str, str] = {}
    for review in reviews:
        if not is_bot(review) and review.get("state") in FORMAL_REVIEWS:
            latest[login(review)] = str(review.get("state"))
    return sum(state == "CHANGES_REQUESTED" for state in latest.values())


def bot_requested_changes(reviews: list[dict], head: str) -> list[str]:
    latest: dict[str, dict] = {}
    for review in reviews:
        if is_bot(review) and review.get("state") in FORMAL_REVIEWS:
            latest[login(review)] = review
    return sorted(name for name, review in latest.items() if review.get("state") == "CHANGES_REQUESTED" and review.get("commit_id") == head)


def owner_verdict(pr: int, head: str, evidence: Evidence, repo: str, default: str) -> Verdict:
    ref = evidence.head_ref
    problem = (base_problem(evidence.branch_pulls, evidence.timeline, pr, default)
               or checks_problem(evidence.check_runs, evidence.statuses, evidence.head_runs, head, repo, pr, default, ref, evidence.opened, evidence.workflows_match, owner_approved=True)
               or security_problem(evidence.statuses, evidence.security_run, evidence.security_jobs, head, repo, pr, default, ref, evidence.opened)
               or head_problem(evidence.head_pulls, evidence.head_jobs, pr, head))
    if problem:
        return Verdict("refuse", problem)
    if requested_changes(evidence.reviews):
        return Verdict("refuse", "a reviewer requested changes; the author or the reviewer must resolve it")
    if blockers := bot_requested_changes(evidence.reviews, head):
        return Verdict("refuse", f"{', '.join(blockers)} requested changes on the current head; the author must resolve it, and the owner's approval does not cover it")
    issue = review_issue(evidence.reviews, head)
    bypassed = (*((GATE_PATHS,) if owner_path_problem(evidence.files, evidence.rules) else ()), *((issue[0],) if issue else ()))
    return Verdict("merge", "every gate the owner's approval does not cover passed", bypassed)


def decide(pr: int, head: str, evidence: Evidence, repo: str, default: str, owner_approved: bool = False) -> Verdict:
    if owner_approved:
        return owner_verdict(pr, head, evidence, repo, default)
    ref = evidence.head_ref
    problem = (owner_path_problem(evidence.files, evidence.rules) or base_problem(evidence.branch_pulls, evidence.timeline, pr, default)
               or checks_problem(evidence.check_runs, evidence.statuses, evidence.head_runs, head, repo, pr, default, ref, evidence.opened, evidence.workflows_match)
               or review_problem(evidence.reviews, head) or security_problem(evidence.statuses, evidence.security_run, evidence.security_jobs, head, repo, pr, default, ref, evidence.opened)
               or head_problem(evidence.head_pulls, evidence.head_jobs, pr, head))
    if problem:
        return Verdict("refuse", problem)
    if requested_changes(evidence.reviews):
        return Verdict("refuse", "a reviewer requested changes; the author or the reviewer must resolve it")
    return Verdict("merge", "every gate passed")


class MergeLock:
    def __init__(self, home: Path, bot: str) -> None:
        self.path = home / "merge" / f"{bot}.lock"

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for obsolete in (self.path.with_name(f"{self.path.stem}.json"), self.path.with_name(f"{self.path.stem}.json.lock")):
            obsolete.unlink(missing_ok=True)
        with self.path.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
