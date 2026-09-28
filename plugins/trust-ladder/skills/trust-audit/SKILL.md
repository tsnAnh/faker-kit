---
name: trust-audit
description: Score how far a repository can be trusted to agents, layer by layer (codebase, static analysis, rules and review bots, skills and verification, style guide), with evidence from running its commands. Returns the five highest-leverage fixes and how many agents can safely work in parallel. Use for /trust-audit, "how agent-ready is this repo", before raising agent parallelism, or monthly.
---

# Trust audit

Measure the environment, not the agents. Every score cites evidence you observed this session: a file, a command's output, a timing. A score without evidence is a guess; say so when you cannot observe something.

## 1. Run the baseline checks

1. Run `/check-agent-compatibility` from the agent-compatibility plugin. Keep its overall score and its startup, validation-loop, and docs-reliability scores.
2. From a clean checkout, run the repository's documented setup and its full `check` or CI-equivalent command. Record the wall time and whether it passed.
3. If a verification skill exists (`verify-<app>` in `.agents/skills` or `.claude/skills`), run its doctor command and drive one feature from its feature map.

## 2. Score each layer from 0 to 3

| Layer | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| 1. Codebase | No stated structure | Structure documented | Boundaries enforced by build or tests; strict compiler modes | One paved path per concern with alternatives deleted; no comment-justified workarounds |
| 2. Static analysis | No linter in CI | Linter runs but warnings pass | Strict rules fail CI; baselines ratchet down | Every recurring correction in the ledger has a rule; `check` runs in under 15 minutes |
| 3. Rules and review bots | No AGENTS.md | AGENTS.md exists but its commands fail or drift | Accurate AGENTS.md with `## Review guidelines`; one review bot | Nested rules where needed; Claude and Codex review on every PR; bot findings triaged with a learned-patterns file |
| 4. Skills and verification | No way for an agent to run the app | Manual run instructions only | A verification skill with a CLI that drives the real app | Verification skill plus a feature map maintained daily; it ran green in step 1 |
| 5. Style guide | Rules live in reviewers' heads | STYLE.md exists | Entries have bad and good examples | Every entry names its `promotion:` layer, and promotions happen |

Also read `.agents/corrections.tsv` if it exists. Count corrections per layer and how many repeat the same rule. A growing share in layers 1 and 2 means the team is climbing.

## 3. Check whether non-engineers can use it

Not a layer; a yes or no for each, with evidence:

- **No install.** A fresh cloud session boots with `scripts/cloud-setup.sh` and `/start-here` resolves in it, in each agent the team uses (in Codex cloud, `$start-here` through `scripts/install-kit-skills.sh`).
- **Fast enough.** `check` passes in that session within ten minutes of its start.
- **Pictures reach people.** The last few `request-a-change` pull requests (label `requested-change`) show before and after images on the page, or a preview link.
- **Every surface has proof.** AGENTS.md has a `Cloud sessions` section naming, for each surface, how it is proven in the cloud or where else.
- **A door they already use.** The request-a-change issue form, with a routine or Codex's GitHub integration picking it up.
- **Safe to say yes.** Branch protection requires review from code owners and passing checks.

Each "no" is a fix for step 4, done by `setup-cloud-agents` or `setup-routines`.

## 4. Pick the five highest-leverage fixes

Rank by how many future corrections a fix prevents, not by effort. Name the skill that does each fix: `setup-team-kit`, `setup-lint-pack`, `encode-lint-rule`, `correct`, `paved-path`, `gardener`, `setup-review-bots`, `/create-verification-skill`, `/maintain-verification-skill`, `setup-cloud-agents`, or `style-guide-from-reviews`.

A missing or failing verification skill is always in the top two when the repository has a user-facing surface.

## 5. Recommend a parallelism level

| When | Safe parallelism |
|---|---|
| Layer 4 below 2, or `check` fails | 1 to 5 agents, each watched by a person |
| Every layer at 2 or more | 10 to 30 agents in cloud sessions, each opening its own PR |
| Every layer at 3, `check` under 15 minutes, both review bots on | 100 or more through pstack's Orchestrate and Autopilot playbooks, or routines |

The lowest layer sets the level. One weak layer means agents will produce work nobody can trust at volume.

## 6. Record and report

Append one tab-separated row to `.agents/trust-audit.tsv`, creating the header if needed:

```text
date	codebase	static	rules	skills	style	compat	check_minutes	parallelism
```

Report:

1. A table of the five layer scores, each with its evidence.
2. The agent-compatibility score.
3. The five fixes, in order, each with the skill to run.
4. The recommended parallelism level and the one layer holding it back.
5. Whether designers, marketers, and founders can use the repository from a browser today, and the first "no" to fix.
6. The change since the previous row in `.agents/trust-audit.tsv`, if there is one.
