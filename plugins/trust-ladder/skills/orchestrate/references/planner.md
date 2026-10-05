Operating manual for root and subplanners. Dispatchers read `dispatcher.md`.

# Planner

Root and subplanners behave the same way. The root reports to the user; a subplanner reports to its parent.

## Prerequisites

Read `runtimes.md` for where each task can run and what each runtime can do. Scripts expect `bun` on PATH. Install dependencies with `bun install` inside this skill's `scripts/` directory. Below, `cli.ts` means `bun <skill>/scripts/cli.ts`; your spawn prompt gives the absolute path.

You run on the operator's machine, in a git worktree of the repository with your own branch checked out. Your children start from the remote, so anything they need from you must be committed and pushed: `syncStateToGit` does that for `state.json` and handoffs.

Regenerate `schemas/*.json` from `scripts/schemas.ts` with `bun run generate-schemas` in `scripts/` after plan or state shape changes.

Slack visibility uses `SLACK_BOT_TOKEN`. Required scopes:

- `chat:write` — post and edit messages.
- `chat:write.customize` — set custom username and icon on bot messages.
- `chat:write.public` — post in public channels without joining first.
- `files:write` — upload handoff artifacts.
- `files:read` — paired with `files:write` for the upload v2 flow.
- `reactions:read` — watch the Andon `:rotating_light:` reaction on the kickoff message.
- `channels:history` — read thread replies via `conversations.replies`. Add `groups:history` instead if the run thread lives in a private channel.

Optional:

- `users:read.email` — best-effort first-name lookup against the dispatcher's git email. Without it, pass `--dispatcher-name` explicitly.

Until those scopes land, Slack calls fail with Slack's `missing_scope` error in `attention.log`. The run still proceeds because git and disk are authoritative.

## Source of truth

Git and disk are the substrate.

- `plan.json` carries the task graph, Slack config, repo URL, and model choices.
- `state.json` carries task status, agent/run ids, branch names, and Slack message timestamps.
- `handoffs/*.md` carries worker and verifier output.
- `attention.log` carries operator-visible failures and decisions.

Slack is human visibility, not task state. The script posts one kickoff message, mirrors task status in that thread, and reads `:rotating_light:` on the kickoff message for Andon. After kickoff, Slack writes stay in the run thread; the adapter requires `threadTs` for those writes. If Slack is down, orchestration correctness does not change.

Orchestrate owns Slack status mirrors, Andon, and the comment retry queue. Agents can still call MCPs directly for Linear, GitHub, Slack, Notion, and other ad-hoc external work. Those systems are not orchestrate destinations.

## Phase 1: publish tasks

Write `plan.json` at `<workspace>`; the default workspace is `.orchestrate/<rootSlug>/`.

```json
{
  "$schema": "<path-to-orchestrate>/schemas/plan.schema.json",
  "goal": "<verbatim user goal>",
  "summary": "ship the dark-mode toggle end to end",
  "rootSlug": "dark-mode",
  "baseBranch": "main",
  "repoUrl": "https://github.com/example-org/example-repo",
  "backend": "claude",
  "runtime": "cloud",
  "tasks": [
    {
      "name": "frontend-toggle",
      "type": "worker",
      "scopedGoal": "Add a Settings UI toggle that flips `useDarkMode` in localStorage.",
      "pathsAllowed": ["packages/ui/src/settings/**"],
      "acceptance": ["Toggle renders in Settings > Appearance"]
    }
  ]
}
```

`summary` is for the human in the Slack thread; `goal` is the agent's full context. Kickoff falls back to a truncated `goal` when `summary` is unset.

Agent settings, on the plan and overridable per task:

| Field | Values | Meaning |
|---|---|---|
| `backend` | `claude` (default), `codex` | Which agent runs the task |
| `runtime` | `cloud` (default), `local` | Where workers and verifiers run. Subplanners always run locally |
| `codexEnv` | an environment id | Required for a Codex task in the cloud |
| `localSandbox` | `full` (default), `workspace` | Plan only. What local processes may do |
| `maxDurationMinutes` | 5 to 10080 | When a run without a handoff counts as failed (`cap-hit`). Defaults: 240 cloud, 180 local, 1440 planners |

Put a verifier on the other backend from its worker when both are available. Prefer `runtime: "local"` for tasks that need this machine (a simulator, a device, local credentials) and for tasks whose prompt grows past what a cloud session takes (`runtimes.md`).

On the first `run --root`, the script uses `plan.slackChannel` for the Slack kickoff and writes `plan.slackKickoffRef`. The root plan gets `slackChannel` from `kickoff --slack-channel`, `run --root --slack-channel`, or `SLACK_CHANNEL_ID`. Subplanners inherit both fields so the whole tree mirrors into one thread.

Planning rules:

- Merges are tasks. Publish a worker whose `scopedGoal` says which branches to merge, conflict intent, and verification. A Codex cloud task cannot fetch other branches it was not started from, so run merge workers on Claude Code or locally.
- Prefer a worker unless you can name the decomposition a subplanner would do.
- One worker can carry a lot. Workers and verifiers are full coding agents with hours of runtime: multi-file slices, multi-step refactors, full repro/fix/test cycles all fit in a single spawn. Each spawn costs agent runtime, Slack noise, and your own coordination overhead. Default to fewer, broader workers; reach for finer granularity only when a slice is genuinely independent or has real contention risk.
- Default to verifiers. Use `type: "verifier"` and `verifies: "<target-task-name>"`.
- Use `verify` for the concrete check recipe. Workers read it as target behavior; verifiers inherit it from their target.
- Set `openPR: true` only for independent worker tasks you want shipped as their own draft PRs.
- Add `measurements[]` for quantitative claims. The script reruns each command on the worker branch after handoff and logs drift.
- Keep fan-in small. If a task needs many upstream handoffs, publish an aggregation worker first.
- Minimize path overlap. List forbidden paths when sibling ownership matters.
- Put task specs in `plan.tasks[]`. Put shared artifacts in git and reference them by path.
- Use the `comment` CLI for Slack notes routed through the retry queue. Use `--criticality required` only for messages that must land. For non-Slack destinations, agents call the relevant tool or MCP directly.

## Phase 2: drive the workspace

All operator actions go through `scripts/cli.ts`.

```bash
bun <skill>/scripts/cli.ts <subcommand> <workspace> [...]
```

`run` spawns pending tasks whose dependencies are satisfied, waits for handoffs, writes handoffs, and repeats until no more progress is possible. Exit code `0` means clean completion. Exit code `100` means a planned checkpoint restart; rerun the same command. Other nonzero codes mean read `state.json` and `attention.log`.

Do not detach `run`. The script is the heartbeat for state, handoffs, Slack mirrors, retry-queue draining, and Andon polling. Agent shells cap a foreground command (Claude Code's Bash tool at 10 minutes), so run it with `--max-runtime-sec 480` and a 10-minute command timeout, and rerun on exit `100`. Agents keep running between runs; the next run reattaches to them. When it exits, call `tree`. If any task is still `pending` or `running`, run the loop again.

`state.json` is the source of truth. Inspect with `tree`, `list`, and `status`.

## Comments

The `comment` CLI is Slack-only and never posts the kickoff. Pass `--task <name>` to validate task context and resolve the run thread, or pass `--thread-ts <ts>` explicitly.

Examples:

```bash
bun cli.ts comment "worker-one is blocked on auth" --task worker-one --workspace .orchestrate/root
bun cli.ts comment "no-repro on the upstream report; need a Linear ticket filed before retrying" --thread-ts 1714500000.000100 --criticality required --workspace .orchestrate/root
```

`--workspace` is required with `--task` and for non-operator file uploads. Operators outside a run enable operator mode with a current-user-owned `~/.orchestrate/operator-mode` file set to `0600`. Workers are assumed unable to write the operator's OS home directory.

Required comments use `comment-retry-queue.json` with the existing backoff schedule.

For external trackers (Linear, GitHub, on-call paging), agents call the relevant MCP directly. Orchestrate does not route those systems.

## Failure recovery

Script handles mechanical liveness. Planner handles meaning.

- Transient spawn failures retry inside `spawnTask`.
- Restarted loops reattach to running tasks via `recoverRunning`, from the run records under `ORCHESTRATE_HOME`.
- An errored run or a blocked handoff is a planner decision: respawn, split, escalate, or drop.
- A cloud session that never delivers its handoff becomes `cap-hit` at `maxDurationMinutes`. Its `handoffs/<task>-failure.md` names the session page; open it to see why before you respawn.
- Downstream tasks stay `pending` when an upstream fails. Fix the upstream and rerun, or `kill` abandoned downstream work.
- Subplanner respawn clones from its own branch after the first attempt so committed child state and handoffs survive.
- `maxAttempts` caps automatic spawning. Bump it in the task definition only when another attempt is intentional.
- Planned checkpoint restarts commit state and handoffs before exiting `100`; rerun the same command.

### Andon

Andon pauses new spawns across the tree. The root polls the Slack kickoff message for `:rotating_light:`. Children read the cached root state through git via `plan.andonStateRef` and `plan.andonStatePath`.

```bash
bun <path-to-orchestrate>/scripts/cli.ts andon raise --reason "<why>" --workspace <workspace>
bun <path-to-orchestrate>/scripts/cli.ts andon clear --workspace <workspace> [--note "<what changed>"]
```

`--reason` is required on `raise`. The root polls the Slack kickoff message for `:rotating_light:` and scans the most recent matching `🚨 ANDON RAISED ...: <reason>` thread reply, then writes the truncated reason into `state.andon.reason`. Children read that cached state via git, so they see *why* orchestration paused without calling Slack themselves. Andon state is operator-typed, capped at 500 chars, and lives in the same trust circle as the rest of `state.json`.

Raise Andon only when continued spawning will produce garbage for the tree: bad upstream output, broken acceptance, or unrecoverable auth/infra. A task's own snag belongs in its handoff, not Andon.

## Finding agents

Use `cli.ts tree <workspace>` and `cli.ts list <workspace>` for lineage, status, and agent IDs. Do not rely on session titles.

`syncStateToGit` defaults on so remote observers can read `state.json` and handoffs from git. Set it to `false` when those artifacts should stay local.

`kill` and `kill-tree` stop local processes. Neither Claude Code nor Codex has a command that stops a cloud session or task, so for those they print the page to stop it from.

```bash
bun cli.ts crawl <local-repo-path> <root-branch> <root-slug>
bun cli.ts kill-tree <local-repo-path> <root-branch> <root-slug> [-y] [--agent-id <id>]
```

Both commands walk `.orchestrate/<rootSlug>/state.json`; every subplanner row recurses into `orch/<rootSlug>/<subplanner-name>`.
