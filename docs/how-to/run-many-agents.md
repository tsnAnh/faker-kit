# Run many agents

More agents only help if you can trust what they produce without reading every line. So the first step is always to measure, and the number of agents follows from the score.

## 1. Measure first

```text
/trust-audit
```

The lowest-scoring layer sets the safe level:

| Repository state | Safe parallelism | How |
|---|---|---|
| No verification skill, or `check` fails | 1 to 5, each watched | Subagents in your own session |
| Every layer at 2 or more | 10 to 30 | Cloud sessions, one pull request each |
| Every layer at 3, `check` under 15 minutes, both review bots required | 100 or more | Playbooks that coordinate agents, and routines |

Fix the top items `/trust-audit` lists before you go up a level. See [Scaling to many agents](../explanation/scaling-agents.md) for why.

## 2. Fan out inside one task

These run many subagents for one goal and return one answer:

```text
take a trace of the status quo, make a targeted fix, and use /swarm to confirm the win
/arena take my prompt to the arena verbatim. i want to compare their proposals with yours.
/interrogate review this pr.
```

`/swarm` fans out independent workers (coverage, races, gauntlets) and aggregates one report.

## 3. Move to cloud sessions

Local worktrees top out at around ten agents: each one costs disk, CPU, and the one emulator your laptop can run. Cloud sessions each get a machine.

```text
/setup-cloud-agents
```

It writes one setup script that boots the repository from nothing and wires it into Claude Code on the web and Codex cloud, then proves it in a fresh session. After that, start a cloud session per task, for example from Pt. 1 of the pstack guide:

```text
/faker-mode improve the initial loading time of our app. first use the verification skill to take a trace of the status quo and identify opportunities. then make a targeted fix, and use it with /swarm to confirm the win.
```

Pi has no managed cloud; Pi users run the same script in a remote container, or use pi-subagents background runs for small fan-outs.

## 4. Let work run while you are away

```text
/faker-mode i'm stepping away. migrate every caller to the new async store; i want to trust it when i'm back.
```

For a long task, `/faker-mode` uses the Autonomous run playbook (a checkable exit condition, then iterate until it holds) or `/figure-it-out` for a large, one-off change. Either keeps a decision trail with `/show-me-your-work`: one row per decision, with the evidence, so you can audit the run afterwards. Pause safely and Session pickup let a run stop cleanly and another agent resume it. The pstack guide's [Run work while you sleep](../../plugins/pstack/docs/guide/07-overnight.md) is the full treatment.

## 5. Run a program

For work that outlives any one agent (a multi-day migration, a queue of independent pull requests):

| Playbook | Use it when |
|---|---|
| Orchestrate | One coordinator session owns a whole project: it writes briefs, runs dozens to hundreds of subagents, keeps the stack green, and checks in with you twice a day |
| Autopilot-full | A queue of independent pull requests, each with its own owner agent carrying it to merged, after the coordinator's verification |
| Autopilot-stack | The same, but delivered as one reviewed stack that you land yourself |

```text
/faker-mode orchestrate the store migration. own it until every package is converted and merged. i'll check in twice a day.
```

## Routines

Routines keep the codebase trustworthy without anyone remembering to: the verification skill stays current, spreading smells get caught early, and bug reports get reproduced before anyone reads them.

```text
/setup-routines
```

| Job | When | Runs |
|---|---|---|
| Maintain the verification skill | Weekday nights | `/maintain-verification-skill` |
| Gardener | Weekday nights | `gardener` over the last 7 days |
| Weekly review | Fridays | `/weekly-review` across every author, posted as an issue |
| Reproduce bug reports | When an issue gets the `bug` label | Reproduce with the verification skill, then open a fix when the cause is clear |

Each job runs either as a **Claude Code routine** (claude.ai/code/routines, or `/schedule`), which runs as its creator's cloud session, or as a **GitHub Actions workflow** from [`templates/github/routines/`](../../plugins/trust-ladder/templates/github/routines/), which runs on repository secrets and works whichever agent each teammate uses. No routine ever merges.

For a full bug-report bot (a feedback channel, triage, reproduction, and fixes), pstack ships [benny](../../plugins/pstack/automations/benny/README.md), an automation pack you install by pointing your agent at its `FOR_AGENTS.md`. `/make-bot-ui` builds a small page whose buttons wake a bot; here, a Claude Code routine through its API trigger.

## Stay in control at scale

- Every agent opens a pull request; none merges on its own. Branch protection and CODEOWNERS make that a rule, not a habit.
- Read decision trails (`decisions.tsv`) rather than diffs when you audit unattended work.
- Rerun `/trust-audit` monthly and before raising parallelism.
