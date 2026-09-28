---
name: setup-review-bots
description: Give every pull request an automatic review from two model families, Claude and Codex, that follows the repository's own REVIEW.md, AGENTS.md review guidelines, and STYLE.md, and set up the triage loop for their comments. Use when setup-team-kit reaches review bots, when a repository has no bot review, or for /setup-review-bots.
---

# Set up review bots

Each teammate uses one coding agent, so a pull request's author sees only one model family. Review bots in CI fill that gap: every pull request gets a Claude review and a Codex review, whoever wrote it. The bots enforce what no linter can, using the repository's written rules. Paths starting with `templates/` are in the trust-ladder plugin.

## 1. Write the rules the bots follow

1. `REVIEW.md` from `templates/REVIEW.md`. Keep the blocking list short and concrete; every item says what to do instead.
2. The `## Review guidelines` section of AGENTS.md. Codex's own code review reads this section, so it should point at REVIEW.md and say what not to comment on.
3. `STYLE.md` from `templates/STYLE.md`, if it does not exist yet.

## 2. Add the workflows

1. Copy `templates/github/claude-review.yml` and `templates/github/codex-review.yml` to `.github/workflows/`.
2. Tell the user which repository secrets to add: `ANTHROPIC_API_KEY` for the Claude workflow and `OPENAI_API_KEY` for the Codex workflow. Never write a key into a file.
3. Model defaults follow the team: Claude Opus 5.5 at max effort, and GPT-6 Sol at high effort in the standard service tier (`service_tier="default"`; the fast tier costs 1.5x quota). Change the `--model`, `model:`, and `effort:` lines only if the team changes models.

Alternatives, when the team prefers them to the workflows:

- Claude: run `/install-github-app` in Claude Code, or turn on Claude Code Review for the organization. Both read REVIEW.md.
- Codex: turn on Codex code review for the repository in Codex settings. It reads the `## Review guidelines` section of AGENTS.md. Anyone can also comment `@codex review` on a pull request.

## 3. Require the reviews

Add both workflows as required status checks on the default branch, together with CI and one human approval (`templates/branch-protection.md`). A bot's `[blocking]` finding must be fixed, or dismissed with a reason, before merge.

## 4. Set up triage

Bots catch real bugs and also file noise. The pstack babysit playbook handles review comments with a fix, dismiss, or ask rubric (`references/bugbot-triage.md` in the pstack plugin; "Bugbot" there means any review bot). Copy `templates/review-bot-patterns.md` to `.agents/review-bot-patterns.md`; that file collects this repository's learned dismissal patterns so each agent does not relearn them.

When the same `[blocking]` comment appears on two pull requests, it is a rule that belongs in a check. Run the `correct` skill on it.

## 5. Prove it

Open a throwaway pull request that breaks one REVIEW.md rule, for example a user-facing string added in one locale only. Both bots must flag it as `[blocking]`. Close the pull request.

## Report

Files added, the secrets the user must set, the branch-protection settings to turn on, and links to the two bot comments from step 5.
