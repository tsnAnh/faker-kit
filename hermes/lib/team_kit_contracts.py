"""The text contracts between Hermes and the coding agent: brief, unit size, report, launch command.

Every reader here is deterministic. Nothing in this module calls a model or the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BRIEF_MARKER = re.compile(r"\A<!-- team-kit:brief v1 card=(?P<card>[A-Za-z0-9_-]+) -->[ \t]*$", re.M)
REPORT_MARKER = "<!-- team-kit:report v1 -->"
LAUNCHED_MARKER = "<!-- team-kit:launched v1 -->"

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
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
DEPENDENCY = re.compile(r"(?<![A-Za-z0-9_.#/-])(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<issue>[0-9]+)\b")
NONE_WORDS = {"none", "none.", "-", "n/a"}


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


def parse_depends(brief: Brief, repo: str) -> list[tuple[str, int]]:
    """The issues this unit waits for, from its Depends on section.

    `OWNER/REPO#12` names an issue in any repository, `#12` one in this unit's repository, and
    "none" means no dependency. Anything else is an error, so a typo cannot drop a dependency.
    """
    text = brief.sections["Depends on"].strip()
    if text.lower() in NONE_WORDS:
        return []
    found: list[tuple[str, int]] = []
    for match in DEPENDENCY.finditer(text):
        ref = (match.group("repo") or repo, int(match.group("issue")))
        if ref not in found:
            found.append(ref)
    if not found:
        raise ContractError("Depends on must be \"none\" or list issues as OWNER/REPO#n or #n")
    return found


SIZE_MARKER = re.compile(r"^<!-- team-kit:size v1 (?P<size>[a-z]+) -->[ \t]*$", re.M)


def size_marker(size: str) -> str:
    """The line team-kit-gh dispatch appends to the issue body, so a later launch knows the unit's size."""
    if size not in UNIT_ATTEMPTS:
        raise ContractError(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
    return f"<!-- team-kit:size v1 {size} -->"


def parse_size(text: str) -> str:
    """The unit's size from its issue body. An issue without the line gets the smallest."""
    found = SIZE_MARKER.findall(text or "")
    size = found[-1] if found else "small"
    if size not in UNIT_ATTEMPTS:
        raise ContractError(f"size {size!r} is not one of {', '.join(UNIT_ATTEMPTS)}")
    return size


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


def launch_prompt(backend: str, repo: str, issue: int) -> str:
    if backend not in LAUNCH_PROMPTS:
        raise ContractError(f"backend must be one of {', '.join(BACKENDS)}")
    if not REPO.match(repo):
        raise ContractError(f"repo {repo!r} is not OWNER/REPO")
    return LAUNCH_PROMPTS[backend].format(repo=repo, issue=int(issue))


def cloud_argv(backend: str, prompt: str, attempts: int = 1, env_id: str | None = None) -> list[str]:
    """The argument list that starts one cloud coding agent. No shell is involved."""
    if backend == "claude":
        return ["claude", "--cloud", prompt]
    if backend != "codex":
        raise ContractError(f"backend must be one of {', '.join(BACKENDS)}")
    if not env_id or not ENV_ID.match(env_id):
        raise ContractError("codex needs the Codex cloud environment id (codex_env in .agents/dispatch.json)")
    if attempts not in UNIT_ATTEMPTS.values():
        raise ContractError("codex attempts must be 1, 2, or 3")
    return ["codex", "cloud", "exec", "--env", env_id, "--attempts", str(attempts), prompt]


def launch_argv(backend: str, repo: str, issue: int, size: str, env_id: str | None = None) -> list[str]:
    """The command that starts the coding agent for one unit. Its prompt starts with faker-mode."""
    if size not in UNIT_ATTEMPTS:
        raise ContractError(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
    return cloud_argv(backend, launch_prompt(backend, repo, issue), UNIT_ATTEMPTS[size], env_id)


def bootstrap_prompt(repo: str, project: str, home: str, backend: str, kit_source: str, kit_ref: str, dispatchers: list[str], codex_env: str | None = None) -> str:
    """The prompt for the one cloud session that prepares a repository for dispatch.

    The repository may not have the kit yet, so the prompt names the kit commit to read skills from
    instead of starting with /faker-mode.
    """
    if not REPO.match(repo) or not REPO.match(home):
        raise ContractError("repo and home must be OWNER/REPO")
    if backend not in BACKENDS:
        raise ContractError(f"backend must be one of {', '.join(BACKENDS)}")
    if not SHA.match(kit_ref):
        raise ContractError("kit_ref must be a full 40-character commit")
    if not dispatchers:
        raise ContractError("trusted_authors in guard.yaml must name the project bot's GitHub login")
    lines = [
        f"Set up {repo} for team-kit dispatch, as one repository of project {project}.",
        f"First clone {kit_source} at commit {kit_ref} into a directory outside this repository.",
        "Read plugins/pstack/skills/faker-mode/SKILL.md there in full and work as it says.",
        "Then follow plugins/trust-ladder/skills/setup-dispatch/SKILL.md there for this repository, with:",
        f"- backend: {backend}",
        f"- kit_ref: {kit_ref}",
        f"- trusted_dispatchers: {', '.join(dispatchers)}",
        f"- project: {project}",
        f"- home repository: {home}" + (" (this one, so also write PROJECT.md)" if home.lower() == repo.lower() else ""),
    ]
    if backend == "codex":
        lines.append(f"- codex_env: {codex_env}")
    lines.append("Open exactly one pull request. Never merge it.")
    return "\n".join(lines)
