# Start here: founders and leads

Three things the kit does for you: tells you what is happening in the codebase in plain words, measures how far the team can trust agents, and turns the team's review feedback into checks that enforce themselves.

Install the kit once: [Install the kit](../how-to/install.md). The examples below are for Claude Code. In Codex, start with `$` instead of `/`; in Pi, with `/skill:`.

## Know what shipped and why

```text
/weekly-review for every author on main, not just me
/why did we move payments to a separate service?
/teach me how the sync engine works, as if I were a new engineer
```

`/weekly-review` groups a week of commits into bug fixes, tech debt, and new work. On its own it covers only your commits, so ask for every author, or have the team's weekly routine post it as an issue ([routines](../how-to/run-many-agents.md#routines)). `/why` answers questions about history from pull requests, commits, issues, and any other sources your agent is connected to. `/teach` explains a system plainly, building it up diagram by diagram. `/bro` restates any answer in plain language.

## Measure how much you can trust agents

```text
/trust-audit
```

It scores the repository from 0 to 3 on each layer of [the trust ladder](../explanation/trust-ladder.md) (codebase, static analysis, rules and review bots, skills and verification, style guide), with evidence for each score. It lists the five fixes that would help most and says how many agents can safely work in parallel today. It logs each audit to `.agents/trust-audit.tsv`, so you can watch the scores climb.

The rule of thumb:

| Where the repository is | Safe parallelism |
|---|---|
| No way for an agent to run the app and check its work | 1 to 5 agents, each watched by a person |
| Every layer at 2 or more | 10 to 30 agents in cloud sessions, each opening its own pull request |
| Every layer at 3, checks under 15 minutes, both review bots on | 100 or more, through routines and pstack's autopilot playbooks |

## Let anyone on the team ship safely

Designers, marketers, and you can change the product with `/request-a-change`. The safety comes from the gates, not from the person: every change is a pull request that passes the same checks, gets reviewed by Claude and Codex, and needs an engineer's approval. Turn those gates on with [the branch protection checklist](../../plugins/trust-ladder/templates/branch-protection.md). [Why non-engineers can ship safely](../explanation/why-non-engineers-can-ship-safely.md) explains the reasoning.

## Turn review feedback into rules

```text
/style-guide-from-reviews last 90 days
```

It reads the team's pull request comments, finds the feedback reviewers keep repeating, and writes it into STYLE.md with an example of each. Each entry names the stronger layer it should move to, such as a lint rule. Engineers then run `/correct` to move it there.

## Roll it out

[Roll the kit out to a team](../how-to/roll-out-to-a-team.md) covers the roles (a gardener, an owner for the verification skill), the weekly rituals, and the numbers to watch.
