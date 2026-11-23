from __future__ import annotations

from dataclasses import dataclass

from faker_kit_ci_rule import CI_FOOTER, CI_RULE
from faker_kit_coding_rule import SESSION_CODING_FOOTER, SESSION_CODING_RULE

BRIEF_NOTICE_CHARS = 6_000
BRIEF_NOTICE = "brief is {chars} chars; the usual brief is under 3,000: cut recited rules and hypotheses"
CHARS_PER_TOKEN = 4

ANDON_FOOTER = (
    "\n\nStop rule: before every push run `gh api 'repos/{repo}/issues?labels=agent:andon&state=open' --jq length`; "
    "if not 0, push nothing, say so and end.\n"
    "Never read or print a secret or merge. Subagents: Sonnet at medium, never xhigh, max or Opus.\n"
    "Label rule: when you open your pull request add the label `faker-kit` (create it if missing), "
    "check it is there, put `Label: faker-kit` in the body, and label no other.\n"
)
SUMMARY_FOOTER = (
    "\nClosing summary rule: before ending, post a pull request comment starting `Closing summary:` with Done, Skipped, Open questions, Blockers. "
    "Open the pull request ready, or if you cannot finish a draft whose body begins `Unit: <unit-id>` with that summary. "
    "Never guess an open requirement: copy the existing pattern or leave it out, and list it under Open questions.\n"
)
SUMMARY_RULE = SUMMARY_FOOTER.strip().removeprefix("Closing summary rule: ")
FAKER_FOOTER = (
    "\nFaker-mode rule: smallest change; no documentation file or code comment but a non-obvious why.\n"
)
FAKER_RULE = FAKER_FOOTER.strip().removeprefix("Faker-mode rule: ")


@dataclass(frozen=True)
class Part:
    name: str
    text: str
    held_by_brief: bool = False


def parts_of(brief: str, repo: str) -> list[Part]:
    footers = (
        ("closing summary", SUMMARY_RULE, SUMMARY_FOOTER),
        ("CI", CI_RULE, CI_FOOTER),
        ("faker-mode rule", FAKER_RULE, FAKER_FOOTER),
        ("coding rule", SESSION_CODING_RULE, SESSION_CODING_FOOTER),
    )
    parts = [Part("brief", brief), Part("andon", ANDON_FOOTER.format(repo=repo))]
    for name, rule, footer in footers:
        held = rule in brief
        parts.append(Part(name, "" if held else footer, held))
    return parts


def prompt_of(brief: str, repo: str) -> str:
    return "".join(part.text for part in parts_of(brief, repo))


def tokens_of(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN)


def describe(brief: str, repo: str) -> dict[str, object]:
    parts = parts_of(brief, repo)
    total = sum(len(part.text) for part in parts)
    return {
        "prompt": "".join(part.text for part in parts),
        "components": [{"name": part.name, "chars": len(part.text), "tokens": tokens_of(len(part.text)), "held_by_brief": part.held_by_brief} for part in parts],
        "fixed_chars": total - len(brief),
        "total_chars": total,
        "total_tokens": tokens_of(total),
    }


def brief_notice(brief: str) -> str | None:
    return BRIEF_NOTICE.format(chars=len(brief)) if len(brief) > BRIEF_NOTICE_CHARS else None
