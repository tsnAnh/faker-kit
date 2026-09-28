# Capture lessons from past sessions

Every session teaches the agent something, and most of it is lost when the session ends. These skills mine past sessions and put what they find where the next agent will read it. What they find still goes through [the trust ladder](../explanation/trust-ladder.md): a lesson that can become a check should.

Your agent's sessions live in:

| Agent | Sessions |
|---|---|
| Claude Code | `~/.claude/projects/<project>/*.jsonl` |
| Codex | `~/.codex/sessions/` |
| Pi | `~/.pi/agent/sessions/` |

The pstack and team-workflows skills say `agent-transcripts/` and `~/.agents/projects/`, the upstream locations; your agent reads these paths instead (see the [compat table](../../plugins/trust-ladder/references/compat.md)).

## Right after a hard task

```text
/reflect that took too long. capture what we learned so the next run doesn't repeat it.
```

`/reflect` runs three reviewers over the current session, finds the lessons, and turns each into a concrete edit to an existing skill. Then run `/correct` on any lesson that is really a rule about the code.

## Your own working style

```text
/automate-me
```

It mines your recent sessions for how you like to work (what you always ask for, what you always correct) and writes a personal `<you>-mode` skill that agents follow. Rerun it to update the skill with new evidence.

## The team's recurring corrections

```text
/workflow-from-chats turn my last two weeks of corrections into skills or rules
```

It extracts durable preferences from recent sessions and proposes skills, rules, or workflow docs. For each proposal, ask whether `/correct` can land it at a stronger layer first.

## Keep AGENTS.md learning

```text
/continual-learning
```

It mines sessions for facts the repository's AGENTS.md is missing (commands that changed, a gotcha that cost an hour) and updates it. It also starts on its own: after at least 10 turns and 2 hours since its last run, its hook asks your agent to run it. That hook runs in Claude Code and Codex when `bun` is installed. In Pi, or without `bun`, run it yourself or schedule it weekly with `/setup-routines`. Tune the cadence with `CONTINUAL_LEARNING_MIN_TURNS` and `CONTINUAL_LEARNING_MIN_MINUTES`.

## Pick up where you left off

```text
/recall the work i did yesterday on virtualization and then read this bug report
```

`/recall` rebuilds your working context from past sessions, live state, and the shared record, and hands back a short brief.

## Lessons from review

```text
/style-guide-from-reviews last 90 days
```

It reads human review comments on merged pull requests, finds what reviewers keep repeating, and writes STYLE.md entries with examples. Each entry names the layer it should climb to next; `/correct` moves it there.
