"""The text contracts between Hermes and the coding agent: brief, unit size, report, launch command.

Every reader here is deterministic. Nothing in this module calls a model or the network.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

BRIEF_MARKER = re.compile(r"\A<!-- team-kit:brief v1 card=(?P<card>[A-Za-z0-9_-]+) -->[ \t]*$", re.M)
REPORT_MARKER = "<!-- team-kit:report v1 -->"
LAUNCHED_MARKER = "<!-- team-kit:launched v1 -->"
VERIFY_LAUNCHED_MARKER = "<!-- team-kit:verify-launched v1 -->"
VERDICT_MARKER = "<!-- team-kit:verdict v1 -->"
# Strongest claim first. The verify-unit skill defines each; a verifier never rounds up.
VERIFICATIONS = ("live-ui-verified", "unit-test-verified", "type-check-only", "verifier-blocked", "verifier-failed")
# The verdicts that let a unit wait for the owner's merge, unless .agents/dispatch.json says otherwise.
DEFAULT_ACCEPTED = ("live-ui-verified", "unit-test-verified")
VERDICT_FIELDS = ("verification", "pr", "head", "sig")
VERIFIER_BACKENDS = ("claude", "codex", "none")
SECURITY_LAUNCHED_MARKER = "<!-- team-kit:security-launched v1 -->"
SECURITY_MARKER = "<!-- team-kit:security v1 -->"
SECURITY_FIELDS = ("range", "findings", "sig")
SEVERITIES = ("high", "medium", "low")
# With no earlier review, the first one covers this many commits up to the head.
FIRST_REVIEW_COMMITS = 20
INCIDENT_MARKER = "<!-- team-kit:incident v1 -->"
TRIAGE_LAUNCHED_MARKER = "<!-- team-kit:triage-launched v1 -->"
DIAGNOSIS_MARKER = "<!-- team-kit:diagnosis v1 -->"
DIAGNOSIS_FIELDS = ("suspect", "confidence", "sig")
CONFIDENCES = ("high", "medium", "low")

BRIEF_SECTIONS = ("Goal", "Scope", "Context", "Acceptance", "Verify", "Timebox", "Forbidden", "Depends on", "Standing orders")
REPORT_STATUSES = ("done", "partial", "blocked")
REPORT_FIELDS = ("status", "branch", "head", "pr")

UNIT_ATTEMPTS = {"small": 1, "medium": 2, "large": 3}
BACKENDS = ("claude", "codex")
PROMPT_STDIN = "stdin"
PROMPT_FILE = "prompt-file"
VENDOR_DIR = ".agents/team-kit"
# The second sentence makes a session without the kit fail at once, and visibly: a Claude Code
# session posts a blocked report that tick turns into a gate. A Codex task cannot comment, so it says
# so in its result.
LAUNCH_PROMPTS = {
    "claude": ("/faker-mode Work the brief in {repo}#{issue} using the follow-brief skill. "
               "If the follow-brief skill is not available in this session, comment on the issue with "
               "`<!-- team-kit:report v1 -->` as the first line and `status: blocked` as the second, "
               "say the team-kit skills did not load, and stop."),
    "codex": ("$faker-mode Work the brief in {repo}#{issue} using the follow-brief skill. "
              "If the follow-brief skill is not available in this session, stop and say the team-kit skills did not load."),
}
# A verifier is a separate session that did not write the change. It checks out the pull request's
# head, proves each Acceptance line, never edits or pushes, and posts a verdict comment.
# The key signs the verdict, so only this session can produce one that counts: the coding agent may
# post as the same GitHub user, but never sees the key. It is never posted anywhere.
VERIFY_PROMPTS = {
    "claude": ("/verify-unit Verify {pr} at commit {head} against the brief in {repo}#{issue}. Sign the verdict with key {key}. "
               "If the verify-unit skill is not available in this session, comment on the issue with "
               "`<!-- team-kit:verdict v1 -->` as the first line and `verification: verifier-blocked` as the second, "
               "say the team-kit skills did not load, and stop."),
    "codex": ("$verify-unit Verify {pr} at commit {head} against the brief in {repo}#{issue}. Sign the verdict with key {key}. "
              "If the verify-unit skill is not available in this session, stop and say the team-kit skills did not load."),
}
# A security reviewer reads the default branch's new commits and never changes anything. Like a
# verifier, it signs its result with a key only its prompt holds.
SECURITY_PROMPTS = {
    "claude": ("/review-main-security Review {span} on {branch} of {repo}, and post the result on {repo}#{issue}. "
               "Sign the result with key {key}. "
               "If the review-main-security skill is not available in this session, comment on that issue with "
               "`<!-- team-kit:security v1 -->` as the first line, say the team-kit skills did not load, and stop."),
    "codex": ("$review-main-security Review {span} on {branch} of {repo}, and post the result on {repo}#{issue}. "
              "Sign the result with key {key}. "
              "If the review-main-security skill is not available in this session, stop and say the team-kit skills did not load."),
}
# A triage session investigates one incident and changes nothing. It may run while the andon cord
# is pulled: diagnosing the incident is what the stopped line waits for.
TRIAGE_PROMPTS = {
    "claude": ("/triage-incident Investigate the incident in {repo}#{issue}: {what}. The head of {branch} is {head}; "
               "its last commit with passing checks is {green}. Post the diagnosis on {repo}#{issue}. Sign it with key {key}. "
               "If the triage-incident skill is not available in this session, comment on that issue with "
               "`<!-- team-kit:diagnosis v1 -->` as the first line, say the team-kit skills did not load, and stop."),
    "codex": ("$triage-incident Investigate the incident in {repo}#{issue}: {what}. The head of {branch} is {head}; "
              "its last commit with passing checks is {green}. Post the diagnosis on {repo}#{issue}. Sign it with key {key}. "
              "If the triage-incident skill is not available in this session, stop and say the team-kit skills did not load."),
}
BRANCH = re.compile(r"\A(?!.*\.\.)(?!/)(?!.*/\Z)[A-Za-z0-9_./-]{1,100}\Z")
REPO = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
ENV_ID = re.compile(r"\A[A-Za-z0-9_-]+\Z")
SHA = re.compile(r"\A[0-9a-f]{40}\Z")
DEPENDENCY = re.compile(r"(?<![A-Za-z0-9_.#/-])(?:(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<issue>[0-9]+)\b")
NONE_WORDS = {"none", "none.", "-", "n/a"}


class ContractError(ValueError):
    pass


@dataclass(frozen=True)
class CloudCommand:
    argv: tuple[str, ...]
    prompt: str
    prompt_transport: str


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
class Verdict:
    verification: str
    pr: str | None
    head: str | None
    sig: str | None = None


@dataclass(frozen=True)
class SecurityResult:
    base: str | None
    head: str
    counts: dict[str, int]
    sig: str | None = None

    @property
    def serious(self) -> int:
        return self.counts["high"] + self.counts["medium"]


@dataclass(frozen=True)
class Diagnosis:
    suspect: str | None
    confidence: str
    sig: str | None = None


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

    `#12` names an issue in this unit's repository, as does `OWNER/REPO#12` (team-kit-gh refuses any
    other repository), and
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
        raise ContractError("Depends on must be \"none\" or list issues as #n")
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


def parse_verdict(text: str) -> Verdict:
    """Read a verifier's verdict header. The verification line is the first line after the marker."""
    body = (text or "").lstrip()
    if not body.startswith(VERDICT_MARKER):
        raise ContractError("verdict must start with <!-- team-kit:verdict v1 -->")
    lines = body[len(VERDICT_MARKER):].strip().splitlines()
    fields: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([a-z]+):\s*(\S.*?)\s*$", line)
        if not match or match.group(1) not in VERDICT_FIELDS:
            break
        fields[match.group(1)] = match.group(2).strip("`")
    if not lines or not lines[0].startswith("verification:"):
        raise ContractError("verdict's first line must be `verification: <value>`")
    verification = fields.get("verification", "")
    if verification not in VERIFICATIONS:
        raise ContractError(f"verification {verification!r} is not one of {', '.join(VERIFICATIONS)}")
    return Verdict(verification=verification, pr=fields.get("pr"), head=fields.get("head"), sig=fields.get("sig"))


def verdict_signature(key: str, verification: str, head: str) -> str:
    """HMAC-SHA256 of `<verification>|<head>` under the verifier's key, as hex."""
    return hmac.new(key.encode(), f"{verification}|{head}".encode(), hashlib.sha256).hexdigest()


def verdict_signed(verdict: Verdict, key: str, head: str) -> bool:
    """Whether the verdict was signed by the session that holds `key`, for exactly `head`."""
    if not verdict.sig or not verdict.head or verdict.head.lower() != head.lower():
        return False
    return hmac.compare_digest(verdict.sig.lower(), verdict_signature(key, verdict.verification, head.lower()))


def verify_argv(backend: str, repo: str, issue: int, pr_url: str, head: str, key: str, env_id: str | None = None) -> CloudCommand:
    """The command that starts a verifier session for one commit of one unit's pull request."""
    if not SHA.match(head or ""):
        raise ContractError("the pull request's head must be a full 40-character commit")
    if not re.fullmatch(r"[0-9a-f]{32,}", key or ""):
        raise ContractError("the verifier key must be hex")
    if backend not in VERIFY_PROMPTS:
        raise ContractError(f"verifier_backend must be one of {', '.join(VERIFIER_BACKENDS)}")
    if not REPO.match(repo):
        raise ContractError(f"repo {repo!r} is not OWNER/REPO")
    if not re.fullmatch(rf"https://github\.com/{re.escape(repo)}/pull/[0-9]+", pr_url, re.I):
        raise ContractError(f"{pr_url!r} is not a pull request in {repo}")
    prompt = VERIFY_PROMPTS[backend].format(repo=repo, issue=int(issue), pr=pr_url, head=head, key=key)
    return cloud_argv(backend, prompt, 1, env_id)


def parse_security(text: str) -> SecurityResult:
    """Read a security review's header: `range: <base|none>..<head>`, `findings: N high, N medium, N low`, `sig:`."""
    body = (text or "").lstrip()
    if not body.startswith(SECURITY_MARKER):
        raise ContractError("a security result must start with <!-- team-kit:security v1 -->")
    fields: dict[str, str] = {}
    for line in body[len(SECURITY_MARKER):].strip().splitlines():
        match = re.match(r"^([a-z]+):\s*(\S.*?)\s*$", line)
        if not match or match.group(1) not in SECURITY_FIELDS:
            break
        fields[match.group(1)] = match.group(2).strip("`")
    span = re.fullmatch(r"([0-9a-f]{7,40}|none)\.\.([0-9a-f]{40})", fields.get("range", ""))
    if not span:
        raise ContractError("range must be `<base commit or none>..<full head commit>`")
    counts = {}
    for severity in SEVERITIES:
        found = re.search(rf"(?:^|,)\s*([0-9]+)\s+{severity}\b", fields.get("findings", ""))
        if not found:
            raise ContractError("findings must read `N high, N medium, N low`")
        counts[severity] = int(found.group(1))
    base = None if span.group(1) == "none" else span.group(1)
    return SecurityResult(base=base, head=span.group(2), counts=counts, sig=fields.get("sig"))


def security_signature(key: str, counts: dict[str, int], head: str) -> str:
    """HMAC-SHA256 of `<high>|<medium>|<low>|<head>` under the reviewer's key, as hex."""
    message = "|".join(str(int(counts[s])) for s in SEVERITIES) + f"|{head.lower()}"
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def security_signed(result: SecurityResult, key: str, head: str) -> bool:
    if not result.sig or result.head.lower() != head.lower():
        return False
    return hmac.compare_digest(result.sig.lower(), security_signature(key, result.counts, head))


def security_argv(backend: str, repo: str, issue: int, branch: str, base: str | None, head: str, key: str,
                  env_id: str | None = None) -> CloudCommand:
    """The command that starts one security review of the default branch from base to head."""
    if backend not in SECURITY_PROMPTS:
        raise ContractError(f"security_backend must be one of {', '.join(VERIFIER_BACKENDS)}")
    if not REPO.match(repo):
        raise ContractError(f"repo {repo!r} is not OWNER/REPO")
    if not BRANCH.match(branch or ""):
        raise ContractError(f"{branch!r} is not a branch name")
    if not SHA.match(head or "") or (base is not None and not SHA.match(base)):
        raise ContractError("the review's commits must be full 40-character SHAs")
    if not re.fullmatch(r"[0-9a-f]{32,}", key or ""):
        raise ContractError("the reviewer key must be hex")
    span = f"the commits after {base} up to {head}" if base else f"the last {FIRST_REVIEW_COMMITS} commits up to {head}"
    prompt = SECURITY_PROMPTS[backend].format(span=span, branch=branch, repo=repo, issue=int(issue), key=key)
    return cloud_argv(backend, prompt, 1, env_id)


def parse_diagnosis(text: str) -> Diagnosis:
    """Read a triage session's header: `suspect: <commit|none>`, `confidence: high|medium|low`, `sig:`."""
    body = (text or "").lstrip()
    if not body.startswith(DIAGNOSIS_MARKER):
        raise ContractError("a diagnosis must start with <!-- team-kit:diagnosis v1 -->")
    fields: dict[str, str] = {}
    for line in body[len(DIAGNOSIS_MARKER):].strip().splitlines():
        match = re.match(r"^([a-z]+):\s*(\S.*?)\s*$", line)
        if not match or match.group(1) not in DIAGNOSIS_FIELDS:
            break
        fields[match.group(1)] = match.group(2).strip("`")
    suspect = fields.get("suspect", "")
    if suspect != "none" and not SHA.match(suspect):
        raise ContractError("suspect must be a full 40-character commit or none")
    if fields.get("confidence") not in CONFIDENCES:
        raise ContractError(f"confidence must be one of {', '.join(CONFIDENCES)}")
    return Diagnosis(suspect=None if suspect == "none" else suspect, confidence=fields["confidence"], sig=fields.get("sig"))


def diagnosis_signature(key: str, suspect: str | None, confidence: str, issue: int) -> str:
    """HMAC-SHA256 of `<suspect or none>|<confidence>|<issue>` under the triage session's key, as hex."""
    message = f"{(suspect or 'none').lower()}|{confidence}|{int(issue)}"
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def diagnosis_signed(diagnosis: Diagnosis, key: str, issue: int) -> bool:
    if not diagnosis.sig:
        return False
    return hmac.compare_digest(diagnosis.sig.lower(), diagnosis_signature(key, diagnosis.suspect, diagnosis.confidence, issue))


def triage_argv(backend: str, repo: str, issue: int, what: str, branch: str, head: str, green: str | None, key: str,
                env_id: str | None = None) -> CloudCommand:
    """The command that starts one read-only triage session for an incident."""
    if backend not in TRIAGE_PROMPTS:
        raise ContractError(f"triage_backend must be one of {', '.join(VERIFIER_BACKENDS)}")
    if not REPO.match(repo):
        raise ContractError(f"repo {repo!r} is not OWNER/REPO")
    if not BRANCH.match(branch or ""):
        raise ContractError(f"{branch!r} is not a branch name")
    if not SHA.match(head or "") or (green is not None and not SHA.match(green)):
        raise ContractError("the incident's commits must be full 40-character SHAs")
    if not re.fullmatch(r"[0-9a-f]{32,}", key or ""):
        raise ContractError("the triage key must be hex")
    if not re.fullmatch(r"[A-Za-z0-9 _.,:;()/#@+=-]{1,160}", what or ""):
        raise ContractError("the incident's summary must be plain text")
    prompt = TRIAGE_PROMPTS[backend].format(repo=repo, issue=int(issue), what=what, branch=branch, head=head,
                                            green=green or "unknown", key=key)
    return cloud_argv(backend, prompt, 1, env_id)


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


def cloud_argv(backend: str, prompt: str, attempts: int = 1, env_id: str | None = None) -> CloudCommand:
    """The command and private prompt payload that start one cloud coding agent. No shell is involved."""
    if backend == "claude":
        return CloudCommand(("claude", "--cloud"), prompt, PROMPT_FILE)
    if backend != "codex":
        raise ContractError(f"backend must be one of {', '.join(BACKENDS)}")
    if not env_id or not ENV_ID.match(env_id):
        raise ContractError("codex needs the Codex cloud environment id (codex_env in .agents/dispatch.json)")
    if attempts not in UNIT_ATTEMPTS.values():
        raise ContractError("codex attempts must be 1, 2, or 3")
    return CloudCommand(("codex", "cloud", "exec", "--env", env_id, "--attempts", str(attempts), "-"), prompt, PROMPT_STDIN)


def launch_argv(backend: str, repo: str, issue: int, size: str, env_id: str | None = None) -> CloudCommand:
    """The command that starts the coding agent for one unit. Its prompt starts with faker-mode."""
    if size not in UNIT_ATTEMPTS:
        raise ContractError(f"size must be one of {', '.join(UNIT_ATTEMPTS)}")
    return cloud_argv(backend, launch_prompt(backend, repo, issue), UNIT_ATTEMPTS[size], env_id)


def bootstrap_prompt(repo: str, project: str, backend: str, kit_ref: str, dispatchers: list[str], codex_env: str | None = None) -> str:
    """The prompt for the one cloud session that prepares a repository for dispatch.

    team-kit-admin vendor has already put the kit in the repository at .agents/team-kit/, so the
    session reads its skills from there. It never clones the kit, which may be private.
    """
    if not REPO.match(repo):
        raise ContractError("repo must be OWNER/REPO")
    if backend not in BACKENDS:
        raise ContractError(f"backend must be one of {', '.join(BACKENDS)}")
    if not SHA.match(kit_ref):
        raise ContractError("kit_ref must be a full 40-character commit")
    if not dispatchers:
        raise ContractError("trusted_authors in guard.yaml must name the project bot's GitHub login")
    lines = [
        f"Set up {repo} for team-kit dispatch, as project {project}.",
        f"The team kit is vendored in this repository at {VENDOR_DIR}/, at commit {kit_ref}.",
        f"If {VENDOR_DIR}/KIT.json is missing or names another commit, stop and say so. Never edit {VENDOR_DIR}/.",
        f"Read {VENDOR_DIR}/plugins/pstack/skills/faker-mode/SKILL.md in full and work as it says.",
        f"Then follow {VENDOR_DIR}/plugins/trust-ladder/skills/setup-dispatch/SKILL.md for this repository, with:",
        f"- backend: {backend}",
        f"- kit_ref: {kit_ref}",
        f"- trusted_dispatchers: {', '.join(dispatchers)}",
        f"- project: {project}",
    ]
    if backend == "codex":
        lines.append(f"- codex_env: {codex_env}")
    lines.append("Open exactly one pull request. Never merge it.")
    return "\n".join(lines)
