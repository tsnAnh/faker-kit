# Start here: engineers

Your first hour with the kit. By the end, a repository is set up for agents, an agent has shipped a change with proof that it works, and one of your corrections is enforced by CI instead of living in a chat.

## 1. Install (5 minutes)

Follow [Install the kit](../how-to/install.md) for your agent, then [set the team model](../how-to/models.md). The examples below use Claude Code's `/name`. In Codex type `$name`; in Pi, `/skill:name`.

## 2. Set up a repository (20 minutes)

In the repository, on a new branch:

```text
/setup-team-kit
```

It writes AGENTS.md (with one `scripts/check.sh` that runs everything CI runs), installs the lint pack for each stack it detects with a baseline so only new violations fail, adds Claude and Codex PR review, and links project skills for all three agents. It opens one pull request. Details: [Set up a repository](../how-to/set-up-a-repo.md).

## 3. Build a verification skill (20 minutes, then ongoing)

```text
/create-verification-skill
```

This is the most valuable thing you can give an agent: a small CLI that starts your app, drives it, and captures screenshots, logs, and traces, plus a feature map of how to reach every screen. Without it, agents can only guess whether their change works, and you stay the bottleneck. For Android, iOS, Flutter, and KMP apps, it builds on `control-mobile`. Details: [Build a verification skill](../how-to/build-a-verification-skill.md).

## 4. Ship one change with proof (15 minutes)

```text
/poteto-mode add an empty state to the search screen. verify it really works.
```

`/poteto-mode` picks a playbook (here, Feature), delegates the work to subagents, runs your verification skill, and opens a pull request with the evidence. It stays on for the rest of the session; say "exit poteto mode" to turn it off. Read the pstack guide's [Route work through `/poteto-mode`](../../plugins/pstack/docs/guide/02-poteto-mode.md) for how to phrase tasks.

## 5. Correct the agent once, for good

The next time you catch an agent doing something you do not want repeated:

```text
/correct the agent keeps catching Exception and swallowing it
```

It picks the strongest layer that can hold the rule (a type, a lint rule, an AGENTS.md line, a skill step, or a STYLE.md entry), proves the rule fails on a bad example and passes on a good one, and logs it in `.agents/corrections.tsv`. Details: [Correct an agent](../how-to/correct-an-agent.md).

## What to learn next

| When you want to | Use | Guide |
|---|---|---|
| Understand code before changing it | `/how`, `/why`, `/teach`, `/recall` | [pstack guide: understand](../../plugins/pstack/docs/guide/03-understand.md) |
| Decide on a design with evidence | `/architect`, `/arena`, the Prototype playbook | [Design before building](../how-to/design-before-building.md) |
| Review and land work | `/interrogate`, `/thermos`, `/deslop`, `/no-comments`, the Babysit playbook | [Build, review, and ship](../how-to/build-review-ship.md) |
| Run agents while you are away | the Autonomous run, Orchestrate, and Autopilot playbooks, routines | [Run many agents](../how-to/run-many-agents.md) |
| Stop bad patterns from spreading | `gardener`, `paved-path` | [Garden the codebase](../how-to/garden-the-codebase.md) |
| Know how many agents a repo can take | `/trust-audit` | [Scaling to many agents](../explanation/scaling-agents.md) |

[Every skill](../reference/skills.md) and [prompts to copy](../reference/prompts.md) are in the reference.
