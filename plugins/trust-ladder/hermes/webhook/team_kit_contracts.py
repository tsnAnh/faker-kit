"""The text contracts between Hermes and the coding agent: brief, report, PR marker, launch command.

Every reader here is deterministic. Nothing in this module calls a model or the network.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

BRIEF_MARKER = re.compile(r"\A<!-- team-kit:brief v1 card=(?P<card>[A-Za-z0-9_-]+) -->[ \t]*$", re.M)
REPORT_MARKER = "<!-- team-kit:report v1 -->"
PR_MARKER = re.compile(r"<!-- team-kit:pr v1 card=(?P<card>[A-Za-z0-9_-]+) issue=(?P<issue>[0-9]+) -->")

BRIEF_SECTIONS = ("Goal", "Scope", "Context", "Acceptance", "Verify", "Timebox", "Forbidden", "Depends on", "Standing orders")
REPORT_STATUSES = ("done", "partial", "blocked")
REPORT_FIELDS = ("status", "branch", "head", "pr")

UNIT_ATTEMPTS = {"small": 1, "medium": 2, "large": 3}
BACKENDS = ("claude", "codex")
LAUNCH_PROMPTS = {
    "claude": "/faker-mode Work the brief in {repo}#{issue} using the follow-brief skill",
    "codex": "$faker-mode Work the brief in {repo}#{issue} using the follow-brief skill",
}
REPO = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
ENV_ID = re.compile(r"\A[A-Za-z0-9_-]+\Z")


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class Brief:
    card: str
    sections: dict[str, str]

    @property
    def title(self) -> str:
        goal = " ".join(self.sections["Goal"].split())
        first = re.split(r"(?<=[.!?])\s", goal, maxsplit=1)[0]
        return first if len(first) <= 80 else first[:77].rstrip() + "..."


@dataclass(frozen=True)
class Report:
    status: str
    branch: str | None
    head: str | None
    pr: str | None


def _sections(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else re.match(r"^## +(.+?)\s*$", line)
        if heading:
            if current is not None:
                out[current] = "\n".join(lines).strip()
            current, lines = heading.group(1), []
        elif current is not None:
            lines.append(line)
    if current is not None:
        out[current] = "\n".join(lines).strip()
    return out


def parse_brief(text: str, standing_orders: str | None = None) -> Brief:
    """Parse an issue body. With standing_orders, also require them verbatim."""
    marker = BRIEF_MARKER.match(text)
    if not marker:
        raise ContractError("brief must start with <!-- team-kit:brief v1 card=<id> -->")
    sections = _sections(text)
    missing = [name for name in BRIEF_SECTIONS if not sections.get(name)]
    if missing:
        raise ContractError(f"brief is missing sections: {', '.join(missing)}")
    if standing_orders is not None and standing_orders.strip() not in sections["Standing orders"]:
        raise ContractError("brief's Standing orders differ from .agents/dispatch.md")
    return Brief(card=marker.group("card"), sections=sections)


def brief_card(text: str) -> str | None:
    marker = BRIEF_MARKER.match(text or "")
    return marker.group("card") if marker else None


def parse_report(text: str) -> Report:
    """Read the report header lines. The status line is the first line after the marker."""
    body = (text or "").lstrip()
    if not body.startswith(REPORT_MARKER):
        raise ContractError("report must start with <!-- team-kit:report v1 -->")
    lines = body[len(REPORT_MARKER):].strip().splitlines()
    fields: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([a-z]+):\s*(\S.*?)\s*$", line)
        if not match or match.group(1) not in REPORT_FIELDS:
            break
        fields[match.group(1)] = match.group(2)
    if not lines or not lines[0].startswith("status:"):
        raise ContractError("report's first line must be `status: done | partial | blocked`")
    status = fields.get("status")
    if status not in REPORT_STATUSES:
        raise ContractError(f"report status {status!r} is not one of {', '.join(REPORT_STATUSES)}")
    return Report(status=status, branch=fields.get("branch"), head=fields.get("head"), pr=fields.get("pr"))


def report_status(text: str) -> str | None:
    try:
        return parse_report(text).status
    except ContractError:
        return None


def pr_marker(card: str, issue: int) -> str:
    return f"<!-- team-kit:pr v1 card={card} issue={issue} -->"


def parse_pr_marker(text: str) -> tuple[str, int] | None:
    match = PR_MARKER.search(text or "")
    return (match.group("card"), int(match.group("issue"))) if match else None


def launch_prompt(backend: str, repo: str, issue: int) -> str:
    if backend not in LAUNCH_PROMPTS:
        raise ContractError(f"backend must be one of {', '.join(BACKENDS)}")
    if not REPO.match(repo):
        raise ContractError(f"repo {repo!r} is not OWNER/REPO")
    return LAUNCH_PROMPTS[backend].format(repo=repo, issue=int(issue))


def launch_command(backend: str, repo: str, issue: int, size: str, env_id: str | None = None) -> str:
    """The one shell command the launcher runs. Single quotes keep `$faker-mode` literal."""
    prompt = shlex.quote(launch_prompt(backend, repo, issue))
    if backend == "claude":
        return f"claude --cloud {prompt}"
    if size not in UNIT_ATTEMPTS:
        raise ContractError(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
    if not env_id or not ENV_ID.match(env_id):
        raise ContractError("codex needs the Codex cloud environment id (codex_env in .agents/dispatch.json)")
    return f"codex cloud exec --env {env_id} --attempts {UNIT_ATTEMPTS[size]} {prompt}"
