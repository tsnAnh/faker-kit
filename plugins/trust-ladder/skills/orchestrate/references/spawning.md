# Spawning tasks

Contract between `plan.json` entries and the agents the script starts. Where each task runs, and what each runtime supports, is in `runtimes.md`.

## Branch naming

Every task has a deterministic branch, `orch/<rootSlug>/<task-name>` (a merge worker `merge-<slice>` pushes `orch/<rootSlug>/<slice>`). The script creates it for local runs, tells cloud sessions to push to it, and creates it from the diff for Codex cloud tasks. The worker reports it in its handoff's `## Branch` line, which stays authoritative. Kebab-case is enforced (`TASK_NAME_RE` in `schemas.ts`) so task names feed filesystem paths and prompt text without escaping.

If a downstream task needs an upstream task's code, depend on that upstream task so the script waits for the handoff and uses the recorded branch.

## Agent naming

Agents get a `name` at `Agent.create`, recorded under `ORCHESTRATE_HOME/agents/`, so `list` and `tree` group a run together.

| Spawn site | Agent name |
|------------|------------|
| Root planner (`cli.ts kickoff`) | `<rootSlug>-root` |
| Worker / subplanner / verifier (`spawnTask`) | `<rootSlug>/<taskName>` — echoes the task's branch without the `orch/` prefix |

Each agent id is the script's own (`orch-<uuid>`), known before the agent starts, so prompts can carry it. The backend's session id and page (claude.ai/code, chatgpt.com/codex) are recorded beside it once the agent starts.

## Starting refs and dependencies

| Field | Controls | Default | Pair with |
|-------|----------|---------|-----------|
| `startingRef` | Which branch the agent starts from: the worktree's base, or the branch a cloud session or task clones | `plan.baseBranch` | `dependsOn` when depending on another task's work |
| `dependsOn` | When the task is allowed to spawn | `[]` | The script records the upstream branch from the handoff |

`startingRef` without `dependsOn` gives a point-in-time snapshot: whatever commits exist on that branch at spawn time, which may be nothing. Pair them unless you really mean "start from the current tip, even if empty".

Verifiers default `startingRef` to their target's branch and auto-include the target in `dependsOn`; the planner doesn't need to wire either explicitly.

Any task can set `verify`: a Markdown-formatted plan (setup, automated, manual, gotchas). Workers see it as a target spec; verifiers inherit the target's `verify` as their recipe.

## Spawn design decisions

- **PRs are opt-in per task.** When `openPR: true`, a worker that can push is told to open a draft PR against `plan.prBase ?? plan.baseBranch` with `gh pr create --draft` after pushing; for a Codex cloud task, or when a handoff names no PR, the script opens the draft PR itself with `gh`. Subplanners and verifiers never open PRs; they hand off to their parent. Task-driven PRs give the planner a specific guarantee: this task ships as its own pull request.
- **PR base vs. worker starting ref.** `plan.baseBranch` is the starting ref for workers that don't specify their own. `plan.prBase` (optional, defaults to `baseBranch`) is where openPR workers aim their PRs. Split when you want workers to inherit planner-side setup from the planner's branch but still open PRs against `main` (so each worker PR is mergeable without the planner's branch landing first). Leave `prBase` unset for the classic pattern where worker PRs stack on the planner's branch.
- **No shared integration branch.** Each task is its own island until a merge task consolidates them. An auto-managed integration branch would smuggle planner-level coding decisions into infrastructure, violating Core Principle #1.

## Task prompt contract

The script renders prompts from `scopedGoal`, `pathsAllowed`, `pathsForbidden`, `acceptance`, `startingRef`, `type`, and `openPR`. Fix the plan entry if the prompt doesn't match your intent. Don't patch the prompt template. Every spawned prompt tells the agent:

- It's isolated. No communication with other agents.
- Commit to its task branch and push it (a Codex cloud task leaves a diff instead). No branch renames, merges, or rebases. Open a draft PR only if the task sets `openPR: true` (workers only; subplanners and verifiers never open PRs).
- Final message is the handoff in the structure from `handoffs.md`. That's the only thing the planner reads. The runtime appends a Delivery section saying how that handoff travels back (`runtimes.md`).

Subplanner and root prompts give the absolute path of this skill's `SKILL.md` and CLI, since `orchestrate` loads only on explicit invocation.

Workers cannot ask clarifying questions mid-run. Under-specified `scopedGoal` produces silent drift. Write each task as if you'll never get another chance to steer it.

Cloud sessions do not see this machine's environment, and their own environments may redact values, so do not rely on env vars for data multiple agents must share. Use the planner-authored artifact pattern instead: commit a file to the base branch and reference it by path in `scopedGoal` so clones pick it up. Never paste credentials into `scopedGoal`; it is sent to the model provider and may end up in git history when state sync is on.

## Slack visibility

When `SLACK_BOT_TOKEN` is set, Slack traffic is owned by the script. Agents do not drive lifecycle. The script posts the kickoff thread to `plan.slackChannel`, records the result in `plan.slackKickoffRef`, mirrors task status messages in that thread, and reads `:rotating_light:` on the kickoff message for Andon.

Spawn prompts still include a Slack block because workers may need to leave notes:

- `comment "<note>" --thread-ts <run-thread-ts> --workspace <workspace>` posts a note through the retry queue when silence would hide useful context. `--task <name>` with `--workspace` is also accepted; the CLI validates the task and posts in the run thread.
- For Linear, GitHub, or other external systems, call the relevant MCP directly. Orchestrate's `comment` CLI is Slack-only.

If Slack comments fail, keep working and say what happened in the handoff. Disk handoffs are still authoritative for downstream prompt assembly.

## Tracking & recovery

After each successful spawn the script persists `agentId`, `runId`, and `parentAgentId` to `state.json`, and the runtime keeps a record of each run (process id, session, branch) under `ORCHESTRATE_HOME/runs/`, so a later rerun re-attaches via `Agent.getRun` and reads lineage from disk. A row with partial identity (exactly one of `agentId` / `runId`) on restart is marked `error` with an explanatory note. Rename and respawn, or prune.

After every handoff the script reconciles dependent verifiers' `startingRef`. The actual worker branch is sourced from the handoff body's `## Branch` line, then the run's task branch. Any verifier whose `verifies` points at the just-handed-off task and whose `startingRef` is still the `orch/<rootSlug>/<task>` placeholder is updated to that real branch. Planner-authored `startingRef` overrides win; each propagation logs to `attention.log`. Load also sweeps over already-handed-off rows so state recovered from disk converges before the next spawn.

## Lineage

Each `state.tasks[]` row's `parentAgentId` is the spawning planner's agent id from `plan.selfAgentId` at spawn. `kill-tree --agent-id` walks those parent links downward. If the planner never set `selfAgentId`, children get `parentAgentId: null` and that subtree is skipped for a scoped kill; omit `--agent-id` to cancel the whole tree.

Spawn templates use the child's id as `{{selfAgentId}}` and the parent's as `{{parentAgentId}}`. The id from `Agent.create` is valid before `send()`: the script assigns it.
