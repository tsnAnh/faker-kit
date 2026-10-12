"""How risky a ready pull request is, and who could review it. Rules in code, never a model.

A unit that reaches the owner carries a `risk:low`, `risk:medium`, or `risk:high` label and a
list of suggested reviewers, so the owner knows which merges deserve a careful read. The score
is the highest of a few plain factors: how much changed, which paths it touched, whether tests
were removed, and how strongly it was verified. Hermes never approves or requests a review.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any  # noqa: TID251 - pull request files and commits arrive as untyped JSON from the GitHub API

LEVELS = ("low", "medium", "high")
RISK_LABELS = tuple(f"risk:{level}" for level in LEVELS)
RISK_MARKER = "<!-- team-kit:risk v1 -->"

# A pattern ending in `/` matches that directory anywhere in a path; any other pattern is a glob
# matched against the whole path and against the file name.
HIGH_PATHS = (
    "auth/", "authn/", "authz/", "login/", "session/", "sessions/", "permissions/", "security/",
    "migrations/", "migrate/", "db/migrate/", "schema.sql", "*.sql",
    "payments/", "billing/", "checkout/",
    ".github/workflows/", ".github/actions/", "infra/", "terraform/", "*.tf", "k8s/", "helm/", "deploy/",
    "*secret*", "*credential*", ".env*",
)
MEDIUM_PATHS = (
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
    "requirements*.txt", "pyproject.toml", "poetry.lock", "uv.lock", "Pipfile*",
    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile*", "pom.xml", "build.gradle*", "*.csproj",
    "Dockerfile*", "docker-compose*", "compose*.y*ml", "config/", "settings/", "*.config.*", "openapi.*", "*.proto",
    "CODEOWNERS", ".github/",
)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|[._-](test|spec)\.[A-Za-z0-9]+$|(^|/)test_[^/]+$")
SMALL_LINES, LARGE_LINES = 150, 600
MANY_FILES = 25
CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
MAX_REVIEWERS = 3
# At most this many changed files get a `commits?path=` history lookup for reverts and hotfixes,
# the most-changed first, so a huge pull request cannot spend the API rate limit.
REPAIR_HISTORY_FILES = 25
# A repair commit is one whose subject line (the first line of the message) starts with a marker
# that only reverts and hotfixes use, so a feature commit such as "add rollback support" or
# "Reverting the old layout is now possible" never counts:
#   - `Revert ...`, the subject `git revert` writes ("Revert \"cart total rounding\"");
#   - `revert:` or `revert(scope):`, the conventional-commit revert type;
#   - `hotfix` in any case, alone or as `hotfix:` / `hotfix(scope):` / `Hotfix checkout crash`;
#   - `fix!:` or `fix(scope)!:`, a conventional breaking fix, which teams use to mark a hotfix.
# A plain `fix:` is an ordinary bug fix and does not count.
REPAIR_COMMIT = re.compile(r"\A(?:Revert\b|revert(?:\([^)]*\))?:|[Hh][Oo][Tt][Ff][Ii][Xx]\b|fix(?:\([^)]*\))?!:)")
OVERRIDE_REASON = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9 _.,:/@+-]{0,119}\Z")


@dataclass(frozen=True)
class Risk:
    level: str
    reasons: tuple[str, ...]
    reviewers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"risk:{self.level}"

    def summary(self) -> str:
        why = "; ".join(self.reasons) if self.reasons else "small change in ordinary paths"
        return f"risk {self.level} ({why})"

    def comment(self) -> str:
        reviewers = ", ".join(f"@{r}" for r in self.reviewers) or "none found"
        reasons = "\n".join(f"- {r}" for r in self.reasons) or "- small change in ordinary paths"
        return (f"{RISK_MARKER}\nrisk: {self.level}\nsuggested reviewers: {reviewers}\n\n"
                f"Why, from rules in code (no model):\n{reasons}\n\n"
                "Hermes never approves or requests a review. The owner decides who reads this and merges it.\n")


@dataclass(frozen=True)
class RiskOverride:
    level: str
    paths: tuple[str, ...]
    reason: str = "repository risk override"


def matches(path: str, pattern: str) -> bool:
    path = path.lstrip("/")
    if pattern.endswith("/"):
        return path.startswith(pattern) or f"/{pattern}" in f"/{path}"
    name = path.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern)


def first_hit(paths: list[str], patterns: Iterable[str]) -> list[str]:
    return sorted({p for p in paths for pattern in patterns if matches(p, pattern)})


def listed_paths(prefix: str, paths: list[str]) -> str:
    return prefix + ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")


def normalize_risk_overrides(value: Any) -> tuple[RiskOverride, ...]:  # noqa: ANN401
    if value in (None, []):
        return ()
    if isinstance(value, tuple) and all(isinstance(v, RiskOverride) for v in value):
        return value
    if not isinstance(value, list):
        raise ValueError(".agents/dispatch.json risk_overrides must be a list of {level, paths, reason} rules")  # noqa: TRY004
    overrides: list[RiskOverride] = []
    for index, raw in enumerate(value, 1):
        if isinstance(raw, RiskOverride):
            overrides.append(raw)
            continue
        if not isinstance(raw, dict) or not set(raw) <= {"level", "paths", "reason"}:
            raise ValueError(f".agents/dispatch.json risk_overrides[{index}] must hold only level, paths, and reason")
        level = raw.get("level")
        if level not in ("medium", "high"):
            raise ValueError(f".agents/dispatch.json risk_overrides[{index}].level must be medium or high")
        paths = raw.get("paths")
        if not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths):
            raise ValueError(f".agents/dispatch.json risk_overrides[{index}].paths must be a non-empty list of path patterns")
        reason = raw.get("reason", "repository risk override")
        if not isinstance(reason, str) or not OVERRIDE_REASON.match(reason):
            raise ValueError(f".agents/dispatch.json risk_overrides[{index}].reason must be 1-120 plain characters")
        overrides.append(RiskOverride(level, tuple(paths), reason))
    return tuple(overrides)


def score(files: list[dict[str, Any]], verification: str | None, high_paths: Iterable[str] = (), medium_paths: Iterable[str] = (),
          checks: str = "passed", risk_overrides: object = (), recent_repair_paths: Iterable[str] = (),
          codeowners_unowned_paths: Iterable[str] = (), repair_history_unchecked: int = 0) -> Risk:
    """files: the pull request's files, each {filename, status, additions, deletions}.
    verification: the verifier's verdict, or None when nobody verified it. checks: passed or none.
    recent_repair_paths and codeowners_unowned_paths are exact file paths, never patterns.
    repair_history_unchecked: how many changed files had no revert or hotfix history looked up."""
    paths = [str(f.get("filename") or "") for f in files]
    lines = sum(int(f.get("additions") or 0) + int(f.get("deletions") or 0) for f in files)
    overrides = normalize_risk_overrides(risk_overrides)
    level, reasons = 0, []

    def bump(to: int, why: str) -> None:
        nonlocal level
        level = max(level, to)
        reasons.append(why)

    if lines > LARGE_LINES:
        bump(2, f"{lines} lines changed")
    elif lines > SMALL_LINES:
        bump(1, f"{lines} lines changed")
    if len(files) > MANY_FILES:
        bump(1, f"{len(files)} files changed")
    high = first_hit(paths, (*HIGH_PATHS, *high_paths))
    if high:
        bump(2, listed_paths("touches ", high))
    for override in (o for o in overrides if o.level == "high"):
        hit = first_hit(paths, override.paths)
        if hit:
            bump(2, listed_paths(f"{override.reason}: ", hit))
    medium = [p for p in first_hit(paths, (*MEDIUM_PATHS, *medium_paths)) if p not in high]
    if medium:
        bump(1, listed_paths("touches ", medium))
    for override in (o for o in overrides if o.level == "medium"):
        hit = first_hit(paths, override.paths)
        if hit:
            bump(1, listed_paths(f"{override.reason}: ", hit))
    # Exact paths, compared as strings: a path such as `app/[id]/page.tsx` is not a glob.
    repaired = set(recent_repair_paths)
    recent_repairs = sorted({p for p in paths if p in repaired})
    if recent_repairs:
        bump(1, listed_paths("recent revert or hotfix touched ", recent_repairs))
    if repair_history_unchecked > 0:
        reasons.append(f"revert and hotfix history checked for the {REPAIR_HISTORY_FILES} most-changed files only, "
                       f"{repair_history_unchecked} more not checked")
    unowned_set = set(codeowners_unowned_paths)
    unowned = sorted({p for p in paths if p in unowned_set})
    if unowned:
        bump(1, listed_paths("CODEOWNERS has no owner for ", unowned))
    removed_tests = [f["filename"] for f in files if f.get("status") == "removed" and TEST_PATH.search(str(f.get("filename") or ""))]
    test_files = [f for f in files if TEST_PATH.search(str(f.get("filename") or ""))]
    test_net = sum(int(f.get("additions") or 0) - int(f.get("deletions") or 0) for f in test_files)
    if removed_tests:
        bump(1, "removes tests: " + ", ".join(removed_tests[:3]))
    elif test_files and test_net < -20:
        bump(1, f"tests shrink by {-test_net} lines")
    if checks == "none":
        bump(1, "no checks ran")
    if verification is None:
        bump(1, "not independently verified")
    elif verification == "type-check-only":
        bump(1, "verified by type-check only")
    return Risk(LEVELS[level], tuple(reasons))


def is_repair_commit(commit: dict[str, Any]) -> bool:
    message = str(((commit.get("commit") or {}).get("message")) or commit.get("message") or "")
    subject = message.lstrip().split("\n", 1)[0].strip()
    return bool(REPAIR_COMMIT.match(subject))


def parse_codeowners(text: str) -> list[tuple[str, tuple[str, ...]]]:
    rules = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        pattern, *owners = line.split()
        rules.append((pattern, tuple(o.lstrip("@") for o in owners if o.startswith("@"))))
    return rules


def codeowners_match(path: str, pattern: str) -> bool:
    """The common CODEOWNERS forms: `*`, `*.ext`, `/dir/`, `dir/`, `/path/file`, `dir/**`."""
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    if pattern in ("*", "**"):
        return True
    if pattern.endswith("/**"):
        pattern = pattern[:-2]
    if pattern.endswith("/"):
        return path.startswith(pattern) if anchored else (path.startswith(pattern) or f"/{pattern}" in f"/{path}")
    if "/" not in pattern:
        return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern) or fnmatch.fnmatchcase(path, pattern)
    return fnmatch.fnmatchcase(path, pattern) or path.startswith(pattern.rstrip("/") + "/")


def owners_for(paths: list[str], rules: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    """Each path's owners come from the last rule that matches it, as GitHub reads CODEOWNERS."""
    found: dict[str, int] = {}
    for path in paths:
        for owner in owners_for_path(path, rules):
            found[owner] = found.get(owner, 0) + 1
    return [o for o, _ in sorted(found.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def owners_for_path(path: str, rules: list[tuple[str, tuple[str, ...]]]) -> tuple[str, ...]:
    owners: tuple[str, ...] = ()
    for pattern, rule_owners in rules:
        if codeowners_match(path, pattern):
            owners = rule_owners
    return owners


def unowned_paths(paths: list[str], rules: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    return sorted({path for path in paths if not owners_for_path(path, rules)})


def pick_reviewers(candidates: list[str], exclude: set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for login in candidates:
        if login and login.lower() not in exclude and login.lower() not in {o.lower() for o in out}:
            out.append(login)
        if len(out) == MAX_REVIEWERS:
            break
    return tuple(out)
