# Scaling to many agents

Running a hundred agents is not hard. Running a hundred agents whose work you would merge is. This page explains how the kit decides how many agents a repository can take, and what changes at each level.

## Parallelism follows trust

The number of agents you can usefully run is set by how much of their output you can accept without checking it yourself. More agents on an untrusted repository produce more pull requests nobody can review, more regressions, and more cleanup. So the kit measures before it scales: `/trust-audit` scores each layer of [the trust ladder](trust-ladder.md), and the lowest layer sets the level. One weak layer is enough to make work at volume untrustworthy.

| Level | What makes it safe |
|---|---|
| 1 to 5 agents, each watched | Nothing yet. You are the verification. |
| 10 to 30 agents, one pull request each | Every layer at 2: strict checks in CI, accurate AGENTS.md, a review bot, and a verification skill that drives the real app |
| 100 or more | Every layer at 3: a paved path per concern, every recurring correction encoded, a maintained feature map, both review bots required, and a check that runs in under 15 minutes |

## What changes as you scale

**Where agents run.** Local worktrees top out at around ten agents: disk, CPU, and the one simulator your machine can run. Past that, cloud sessions each get their own machine, which is why Pt. 1 recommends cloud agents over worktrees. `/setup-cloud-agents` makes the repository boot from nothing in Claude Code on the web and Codex cloud.

**How work is shared.** pstack's rule is [separate before serializing shared state](../../plugins/pstack/skills/principle-separate-before-serializing-shared-state/SKILL.md): give each agent its own worktree or machine and its own branch, not a lock on a shared one. One pull request per agent keeps each change reviewable on its own.

**Who coordinates.** Above a few dozen agents, a person cannot track them. pstack's Orchestrate, Autopilot-full, and Autopilot-stack playbooks put one coordinator agent in charge of a program: it writes briefs, spawns workers, verifies each result with an independent swarm, and checks in with you twice a day. The coordinator owns verdicts; it never trusts a worker's own report.

**What you review.** At volume you read evidence and decision trails, not diffs. `/show-me-your-work` keeps one row per decision with its evidence; the verification skill supplies the proof; the review bots and required checks do the line-by-line pass.

**Where diversity comes from.** Each person on this team uses one agent, so their own session sees one model family. The review bots run Claude and Codex on every pull request in CI, so every change still gets two families' scrutiny. Panels in pstack fall back to repeated runs on your own model when the other family is not reachable, which keeps independent samples even when it cannot keep different families.

## The outer loop

Many agents also means agents that start themselves. The talk describes bots subscribed to Slack threads and Sentry alerts that kick off cloud agents, reproduce bug reports, and open pull requests. In this kit, routines do that: nightly verification-skill upkeep, a nightly gardener sweep, a weekly review, and reproducing every issue labeled as a bug. They run as Claude Code routines or GitHub Actions ([Run many agents](../how-to/run-many-agents.md#routines)). No routine merges; each opens a pull request that goes through the same gates as everyone else's.

## Keep measuring

Scaling is not a one-time decision. The corrections ledger shows whether the same mistakes keep coming back; the trust audit shows whether the weakest layer is rising. Raise parallelism when both say so, and lower it when a layer slips.
