---
name: setup-review-bots
description: Give every pull request an automatic review from two model families, Claude and Codex, that follows the repository's own REVIEW.md, AGENTS.md review guidelines, and STYLE.md, and set up the triage loop for their comments. Runs on the agents' own GitHub integrations and the team's subscriptions, with no API keys. Use when setup-team-kit reaches review bots, when a repository has no bot review, or for /setup-review-bots.
---

# Set up review bots

Each teammate uses one coding agent, so a pull request's author sees only one model family. Review bots fill that gap: every pull request gets a Claude review and a Codex review, whoever wrote it. The bots enforce what no linter can, using the repository's written rules. Paths starting with `templates/` are in the trust-ladder plugin.

Both bots run on the agents' own GitHub integrations and draw on teammates' subscriptions, so the repository needs no `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` secret. Never write a key into a file.

## 1. Write the rules the bots follow

1. `REVIEW.md` from `templates/REVIEW.md`. Keep the blocking list short and concrete; every item says what to do instead.
2. The `## Review guidelines` section of AGENTS.md. Codex's code review reads this section, so it should point at REVIEW.md, say what not to comment on, and ask for every blocking rule to be treated as P1, because Codex posts only P0 and P1 findings.
3. `STYLE.md` from `templates/STYLE.md`, if it does not exist yet.
4. `.agents/routines/pr-review.md` from `templates/routines/pr-review.md`. It is the Claude reviewer's instructions, versioned with the code.

## 2. Turn on the Codex review

Someone on the team with a ChatGPT plan that includes Codex, and push or admin access to the repository, does this once, in Codex on the web:

1. Connect GitHub and create an environment for the repository.
2. In Codex settings, turn on code review for the repository, with automatic reviews, so Codex reviews every pull request when it is opened for review.

Anyone can also comment `@codex review` on a pull request, for example to get a fresh review after pushing fixes.

## 3. Turn on the Claude review

One teammate who uses Claude Code on a Pro, Max, Team, or Enterprise plan owns this. It is a Claude Code routine with a GitHub trigger:

1. Install the Claude GitHub App on the repository (github.com/apps/claude). The web form offers it when it is missing.
2. At claude.ai/code/routines, create a routine named `pr-review` with this prompt: "Read `.agents/routines/pr-review.md` on the default branch and follow it for the pull request that started this run." Select Claude Opus 5.5 in the model selector, the repository, and the cloud environment from `setup-cloud-agents` (or **Default**). Remove every connector the review does not need.
3. Add GitHub event triggers for `pull_request.opened` and `pull_request.ready_for_review`, each filtered to **Is draft** `false` and **Base branch** equal to the default branch. Add `pull_request.synchronize` too if the owner's daily routine runs allow a review on every push.

Tell the owner what this costs them: each triggered run uses one of their account's daily routine runs and their plan's usage, and the review comments appear under their GitHub name. If the team opens more pull requests a day than the owner's runs allow, turn on usage credits, split the triggers across two owners (for example, by base branch), or use Claude Code Review instead.

If nobody on the team uses Claude Code, or nobody uses Codex, set up the one you can and say that pull requests get one family's review.

## Alternatives

- **Claude Code Review** (Team and Enterprise). An Owner turns it on in the organization's admin settings. It reads REVIEW.md and CLAUDE.md and is billed to the organization as usage credits per review, not to one person's plan. Its check run is always neutral, so it never blocks a merge on its own.
- **GitHub Actions with API keys.** `templates/github/claude-review.yml` and `templates/github/codex-review.yml` run the reviews in CI as the repository, not a person, and their check runs can be required. They need the `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` repository secrets and bill the API. For the Claude workflow, `claude_code_oauth_token` from `claude setup-token` bills one person's Pro or Max plan instead, but it is still a repository secret. Model defaults follow the team: Claude Opus 5.5 at max effort, and GPT-6 Sol at high effort in the standard service tier (`service_tier="default"`; the fast tier costs 1.5x quota).

## 4. Make the findings count

Review comments are not status checks, so the gate is conversation resolution: in branch protection (`templates/branch-protection.md`), require CI, a code owner's approval, and resolved conversations before merge. A `[blocking]` or P1 finding must be fixed, or answered with a reason and resolved, before the pull request can merge.

## 5. Set up triage

Bots catch real bugs and also file noise. The pstack babysit playbook handles review comments with a fix, dismiss, or ask rubric (`references/bugbot-triage.md` in the pstack plugin; "Bugbot" there means any review bot). Copy `templates/review-bot-patterns.md` to `.agents/review-bot-patterns.md`; that file collects this repository's learned dismissal patterns so each agent does not relearn them.

When the same `[blocking]` comment appears on two pull requests, it is a rule that belongs in a check. Run `/correct` on it.

## 6. Prove it

Open a throwaway pull request that breaks one REVIEW.md rule, for example a user-facing string added in one locale only. Both bots must flag it: Claude as `[blocking]`, Codex as P1 or higher. Close the pull request.

## Report

Files added, who owns each bot and what it draws on (the Codex workspace, the Claude routine owner's account), the branch-protection settings to turn on, and links to the two bot reviews from step 6.
