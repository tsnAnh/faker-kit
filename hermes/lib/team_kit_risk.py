"""How risky a ready pull request is, and who could review it. Rules in code, never a model.

A unit that reaches the owner carries a `risk:low`, `risk:medium`, or `risk:high` label and a
list of suggested reviewers, so the owner knows which merges deserve a careful read. The score
is the highest of a few plain factors: how much changed, which paths it touched, whether tests
were removed, and how strongly it was verified. Hermes never approves or requests a review.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

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


def matches(path: str, pattern: str) -> bool:
    path = path.lstrip("/")
    if pattern.endswith("/"):
        return path.startswith(pattern) or f"/{pattern}" in f"/{path}"
    name = path.rsplit("/", 1)[-1]
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(name, pattern)


def first_hit(paths: list[str], patterns) -> list[str]:
    return sorted({p for p in paths for pattern in patterns if matches(p, pattern)})


def score(files: list[dict], verification: str | None, high_paths=(), medium_paths=(), checks: str = "passed") -> Risk:
    """files: the pull request's files, each {filename, status, additions, deletions}.
    verification: the verifier's verdict, or None when nobody verified it. checks: passed or none."""
    paths = [str(f.get("filename") or "") for f in files]
    lines = sum(int(f.get("additions") or 0) + int(f.get("deletions") or 0) for f in files)
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
        bump(2, "touches " + ", ".join(high[:5]) + (f" and {len(high) - 5} more" if len(high) > 5 else ""))
    medium = [p for p in first_hit(paths, (*MEDIUM_PATHS, *medium_paths)) if p not in high]
    if medium:
        bump(1, "touches " + ", ".join(medium[:5]) + (f" and {len(medium) - 5} more" if len(medium) > 5 else ""))
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
        owners: tuple[str, ...] = ()
        for pattern, rule_owners in rules:
            if codeowners_match(path, pattern):
                owners = rule_owners
        for owner in owners:
            found[owner] = found.get(owner, 0) + 1
    return [o for o, _ in sorted(found.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def pick_reviewers(candidates: list[str], exclude: set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for login in candidates:
        if login and login.lower() not in exclude and login.lower() not in {o.lower() for o in out}:
            out.append(login)
        if len(out) == MAX_REVIEWERS:
            break
    return tuple(out)
