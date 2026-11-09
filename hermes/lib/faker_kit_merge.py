from __future__ import annotations

import contextlib
import fcntl
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MERGE_METHOD = "squash"
REVIEW_WORKFLOW = ".github/workflows/claude-review.yml"
KIT_CHECKS_WORKFLOW = ".github/workflows/faker-kit-checks.yml"
NOT_CI_WORKFLOWS = (REVIEW_WORKFLOW, KIT_CHECKS_WORKFLOW)
WORKFLOW_FOLDER = ".github/workflows/"
BASE_CHANGE_EVENTS = ("base_ref_changed", "automatic_base_change_succeeded", "automatic_base_change_failed")
PASSING_CONCLUSIONS = ("success", "neutral", "skipped")
FORMAL_REVIEWS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
IGNORED_STATUS = "security-review"
TOKEN_CHARS = 40


@dataclass(frozen=True)
class Evidence:
    check_runs: list[dict]
    statuses: list[dict]
    reviews: list[dict]
    head_pulls: list[dict] | None = field(default=None)
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


def review_suites(head_runs: list[dict]) -> set[int]:
    return {run["check_suite_id"] for run in head_runs if isinstance(run.get("check_suite_id"), int) and workflow_path(run) == REVIEW_WORKFLOW}


def review_run_ids(head_runs: list[dict]) -> set[int]:
    return {run["id"] for run in head_runs if isinstance(run.get("id"), int) and workflow_path(run) == REVIEW_WORKFLOW}


def status_run_id(status: dict, repo: str) -> int | None:
    found = run_url(repo).match(str(status.get("target_url") or ""))
    return int(found.group(1)) if found else None


def run_url(repo: str) -> re.Pattern[str]:
    return re.compile(rf"\Ahttps://github\.com/{re.escape(repo)}/actions/runs/([0-9]+)(?:[/?#].*)?\Z", re.I)


def gated_checks(check_runs: list[dict], statuses: list[dict], head_runs: list[dict], repo: str) -> tuple[list[dict], list[dict]]:
    suites, run_ids = review_suites(head_runs), review_run_ids(head_runs)
    kept_runs = [run for run in check_runs if (run.get("check_suite") or {}).get("id") not in suites]
    kept_statuses = [s for s in statuses if s.get("context") != IGNORED_STATUS and status_run_id(s, repo) not in run_ids]
    return kept_runs, kept_statuses


def checks_problem(check_runs: list[dict], statuses: list[dict], head_runs: list[dict] | None, head: str, repo: str, pr: int, default: str, ref: str, opened: str, workflows_match: bool,
                   owner_approved: bool = False) -> str | None:
    if head_runs is None:
        return "the workflow runs on this head could not be read"
    counted_runs, counted_statuses = gated_checks(check_runs, statuses, head_runs, repo)
    failed = pending = 0
    for run in counted_runs:
        if run.get("status") != "completed":
            pending += 1
        elif str(run.get("conclusion") or "").lower() not in PASSING_CONCLUSIONS:
            failed += 1
    for status in counted_statuses:
        state = str(status.get("state") or "").lower()
        pending += state == "pending"
        failed += state not in ("success", "pending")
    if not counted_runs and not counted_statuses:
        return "GitHub reports no checks on the head; the repository needs GitHub Actions CI that builds and tests every pull request"
    if owner_approved and pending and not failed:
        return "checks still running; the owner asks again when they finish"
    if failed or pending:
        return f"checks have not all passed ({failed} failed, {pending} pending)"
    return ci_missing(counted_runs, head_runs, head, repo, pr, default, ref, opened, workflows_match)


def head_problem(pulls: list[dict] | None, pr: int, head: str) -> str | None:
    if pulls is None:
        return "the pull requests on this head could not be read"
    others = sorted(int(p["number"]) for p in pulls
                    if isinstance(p.get("number"), int) and p["number"] != pr and p.get("state") == "open" and (p.get("head") or {}).get("sha") == head)
    if others:
        return f"another open pull request (#{others[0]}) has the same head, so its runs could stand in for #{pr}'s; close it or push a new commit"
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


def requested_changes(reviews: list[dict]) -> int:
    latest: dict[str, str] = {}
    for review in reviews:
        if not is_bot(review) and review.get("state") in FORMAL_REVIEWS:
            latest[login(review)] = str(review.get("state"))
    return sum(state == "CHANGES_REQUESTED" for state in latest.values())


def decide(pr: int, head: str, evidence: Evidence, repo: str, default: str, owner_approved: bool = False) -> Verdict:
    ref = evidence.head_ref
    problem = (base_problem(evidence.branch_pulls, evidence.timeline, pr, default)
               or checks_problem(evidence.check_runs, evidence.statuses, evidence.head_runs, head, repo, pr, default, ref, evidence.opened, evidence.workflows_match, owner_approved)
               or head_problem(evidence.head_pulls, pr, head))
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
