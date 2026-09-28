# Build, review, and ship a change

The loop for one change: build it, prove it, clean it, review it, get it green, land it. pstack's [Build and clean](../../plugins/pstack/docs/guide/05-build-and-clean.md) and [Verify and ship](../../plugins/pstack/docs/guide/06-verify-and-ship.md) chapters go deeper.

## Build

```text
/faker-mode add CSV export to the reports page. verify it really works.
```

Say what you want and how you will know it is done. `/faker-mode` picks the playbook (Feature, Bug fix, Refactoring, Perf issue), delegates to subagents, and stays in charge of the result. `/tdd` works test-first when you want that.

## Prove

Every change that users can see gets evidence from the verification skill: screenshots, a recording, a transcript. Tests alone are not proof that it works. If the repository has no verification skill, [build one](build-a-verification-skill.md) first.

```text
apply prove it works. run the real export and show me the downloaded file.
```

## Clean

```text
/deslop
/no-comments
can we unslop the PR description?
```

- `/deslop` removes AI-generated slop from the branch and cleans up code style.
- `/no-comments` runs the Comment Sicko agent, which flags comments that explain a workaround instead of fixing it, and turns claimed constraints into checks.
- `/unslop` does the same for prose.

## Review

Three layers review every change:

1. **Your agent**, before it opens the pull request: `/interrogate review this pr.` runs a panel of reviewers; `/thermos` runs a security-and-correctness review and a code-quality review in parallel.
2. **The review bots**, on every pull request: Claude and Codex review it against REVIEW.md, the `## Review guidelines` in AGENTS.md, and STYLE.md. Set them up with `/setup-review-bots`. Codex's own code review and a teammate's Claude Code routine run them on GitHub, with no API keys, so every change gets two model families' review, whichever agent its author uses. Comment `@codex review` to ask Codex again after you push fixes.
3. **A human code owner**, who approves. Branch protection requires it.

`/make-pr-easy-to-review` cleans up commit history, improves the description, and adds reviewer guidance without changing behavior. `/pr-review-canvas` renders a pull request as an interactive walkthrough.

## Get it green

```text
/faker-mode babysit this PR until it is merge-ready
```

The Babysit playbook fixes CI failures, answers review comments (fix, dismiss with a reason, or ask), and resolves conflicts. It triages bot findings with pstack's [bugbot triage rubric](../../plugins/pstack/skills/faker-mode/references/bugbot-triage.md) and the repository's `.agents/review-bot-patterns.md`. Standalone tools for the same jobs: `/fix-ci`, `/loop-on-ci`, `/get-pr-comments`, `/fix-merge-conflicts`, `/check-compiler-errors`.

When the same bot finding shows up on two pull requests, it is a rule that belongs in a check: run `/correct` on it.

## Land

```text
/faker-mode ship this stack
```

The Shipping playbook verifies each pull request independently before merging and lands them bottom-up. `/review-and-ship` does a lighter version for a single pull request. `/new-branch-and-pr` starts the next one.
