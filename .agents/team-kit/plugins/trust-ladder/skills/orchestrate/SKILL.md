---
name: orchestrate
description: Use only when the user explicitly types `/orchestrate <goal>` (Codex `$orchestrate <goal>`) to decompose a large task, run a tree of parallel workers, subplanners, and verifiers as Claude Code or Codex agents, in the cloud or as local processes, and collect their structured handoffs; do not invoke autonomously.
disable-model-invocation: true
---

# Orchestrate

An explicit `/orchestrate <goal>` (Codex: `$orchestrate <goal>`) fans a large task out across parallel coding agents. Workers don't talk to each other; they talk up through structured handoffs. The spawn, wait, and handoff loop lives in `scripts/cli.ts`. The planner writes `plan.json`, the script executes it, and the planner reads handoffs to decide what comes next. Long-running agent loops drift; a script with a JSON state file keeps its footing.

Every node is a Claude Code or a Codex agent. Planners always run on this machine, as a headless `claude -p` or `codex exec` process in their own git worktree, because only a local process can start more agents. Workers and verifiers run where the plan says: a Claude Code cloud session (`claude --cloud`), a Codex cloud task (`codex cloud exec`), or a local process in a worktree. `references/runtimes.md` says what each can and cannot do.

## Setup

- `bun` on PATH. Run `bun install` once in `scripts/`.
- `git`, with push access to the repository.
- The agent CLI each node needs, signed in on this machine: `claude` for Claude Code, `codex` for Codex (`codex login`, a ChatGPT account, for Codex cloud).
- For Claude Code cloud sessions: a cloud environment for the repository, set up by `setup-cloud-agents`, so a session can build, test, and push.
- For Codex cloud tasks: the repository's Codex cloud environment id (chatgpt.com/codex environments), as `plan.codexEnv`.
- `SLACK_BOT_TOKEN` is optional. When set, pass `--slack-channel <id>` to `kickoff` or the first `run --root`, or set `SLACK_CHANNEL_ID`. The script posts the kickoff thread, mirrors task status, and reads Andon reactions. When unset, the script logs once and runs without Slack; correctness does not change.

Run state lives under `ORCHESTRATE_HOME` (default `~/.orchestrate`): a record per agent and run, the local worktrees, and the clones cloud sessions start from. Keep it on a machine that stays on while the tree runs.

## Core principles

These rules make the tree self-converging without global coordination.

1. **Planners own scopes and publish tasks. They do no coding.** Writing `plan.json`, reading handoffs, and deciding what's next are planner work. Editing files, running `git merge`, and fixing conflicts inline are not. If a planner feels the urge to code, it publishes a task for a worker instead.
2. **Planners don't know who picks up their tasks.** The script routes each task to an agent. The planner's mental model stays at the task level.
3. **Workers are isolated.** One task, one checkout of the repo, no channel to any other agent. One handoff when done.
4. **Subplanners are recursive planners.** A planner publishes a "subplan this slice" task; the subplanner fully owns that slice and hands back an aggregated handoff.
5. **Continuous motion via handoffs.** A planner that thought it was done can receive a late handoff and replan. No "finished" state until the planner decides to stop publishing.
6. **Propagation, not synchronization.** No cross-talk between siblings. No shared state between levels. Each level sees only its children's handoffs.
7. **The author never grades its own work.** Default to a verifier per worker, and run it on the other agent when you can (`backend` per task), so a second model family checks the change.

## Node types

| Node           | Runs the loop? | Runs where                  | Output                                  |
| -------------- | -------------- | --------------------------- | --------------------------------------- |
| Planner        | yes            | Local process               | User-facing message + optional PR       |
| Subplanner (↻) | yes            | Local process               | Handoff to parent                       |
| Worker         | no             | Cloud or local, per plan    | Handoff to spawning planner             |
| Verifier       | no             | Cloud or local, per plan    | Verdict handoff to spawning planner     |
| Git            | n/a            | The repository's remote     | Branches (code) + handoffs/ (meaning)   |

## Role

Two roles, one skill. Read your role's reference file and skip the other.

**Dispatcher.** You're in an interactive session and the user typed `/orchestrate <goal>`. Start a root planner with `kickoff` and return what it prints, or, if the user wants to steer the plan here, become the root planner yourself. See `references/dispatcher.md`.

**Planner (root or sub).** You were started with a prompt that opens with "You are the root planner for:" or "You are a subplanner for:", or the user chose to plan in this session. You own a scope, publish tasks, read handoffs, decide what's next. See `references/planner.md`, then `references/spawning.md` and `references/handoffs.md`.

`disable-model-invocation: true` means this skill loads only on explicit invocation.
