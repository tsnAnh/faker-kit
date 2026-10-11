# Runtimes

Every task runs on one of four runtimes: `backend` (`claude` or `codex`) times `runtime` (`cloud` or `local`). Planners, the root and every subplanner, always run locally, because only a process on this machine can start more agents. The code is in `scripts/runtime/`; the engine sees one API (`Agent.create`, `agent.send`, `Agent.getRun`, `run.wait`, `run.stream`, `run.cancel`) whichever runtime is behind it.

| | Claude Code, local | Codex, local | Claude Code, cloud | Codex, cloud |
|---|---|---|---|---|
| Starts with | `claude -p --output-format stream-json` | `codex exec --json` | `claude --cloud "<prompt>"` | `codex cloud exec --env <id> --branch <ref>` |
| Works in | A git worktree on the task branch, under `ORCHESTRATE_HOME/worktrees/` | Same | A cloud session cloned from `startingRef` | A cloud task started from `startingRef` |
| Handoff comes back as | The final `result` message | The last agent message (`-o`) | The message of its last commit, `orchestrate-handoff <nonce>`, on the task branch | `.orchestrate-handoff.md` in the task's diff |
| Branch | The agent pushes; the script commits and pushes anything left | Same | The agent pushes | The script applies the diff, commits, and pushes |
| Done when | Its `result` line appears, or the process exits | The process exits | The handoff commit appears on the branch | `codex cloud status` says READY |
| Live events (`tail`) | Yes | Yes | No; watch claude.ai/code | No; watch chatgpt.com/codex |
| Cancel | Kills the process | Kills the process | No command; stop it from its page | No command; stop it from its page |
| Follow-up message | `--resume` the session | `codex exec resume` | `claude -p "<text>" --cloud <session>` | Not possible |
| Model | `--model`, `--effort` | `-m`, `model_reasoning_effort` | The session's default | The environment's model |
| Prompt size | Any (sent on stdin) | Any | Up to 120,000 bytes (one argument) | Any (sent on stdin) |

## One agent per branch

A task branch belongs to one running agent. Starting a second agent on a branch whose agent is still running is refused, so a repeated kickoff or an early respawn never takes a running agent's worktree. Agents push their branch with `--force`: the branch is the task's own, and an earlier attempt may have left it. The script's own pushes use `--force-with-lease` against the tip it just read.

## Choosing

- **Cloud** scales past this machine and needs no worktrees here, but the session must be able to build and test the repository on its own: set the repository up with `setup-cloud-agents` first. Use it for most workers and verifiers.
- **Local** needs only the CLI signed in here. It sees every event, can be cancelled and messaged, and can use what only this machine has (a simulator, a device, local credentials). Each local task holds a worktree and a process, so this machine caps how many run at once.
- **Codex cloud** cannot push, fetch other branches, or reach the network during the agent phase by default. Give it self-contained tasks from one branch. Merge workers belong on Claude Code or a local runtime.
- **A verifier on the other backend** from its worker gets a second model family's view of the change. Set `backend` on the verifier task.

## How the script knows a run ended

Neither CLI offers a command that reads a cloud session's final message, so cloud handoffs travel through git:

- **Claude Code cloud.** The prompt's Delivery section gives the agent a random nonce. When it is done, whatever its status, the agent makes one last, empty commit whose message starts `orchestrate-handoff <nonce>`, followed by the handoff, and pushes the task branch. The loop polls the branch tip (`git ls-remote`) and reads recent commit messages from a bare cache repository under `ORCHESTRATE_HOME/cache/`. A commit with another run's nonce never counts. An empty commit adds nothing to a squash merge.
- **Codex cloud.** The agent writes its handoff to `.orchestrate-handoff.md` and cannot push. When the task is READY, the loop applies `codex cloud diff` to the task branch in a local worktree, takes the handoff file out, commits the rest, and pushes. A diff that no longer applies to `startingRef` is an error.

A cloud session that crashes, stalls, or forgets the handoff shows nothing, so every run has a time budget, `maxDurationMinutes` (default 240 for cloud, 180 for local, 1440 for planners). Past it the run is an error with failure mode `cap-hit`, and a local process is killed. Set it on a task that legitimately runs longer.

## Local permissions

`plan.localSandbox` decides what a local process may do:

- `full` (default): Claude Code `--permission-mode bypassPermissions`, Codex `--dangerously-bypass-approvals-and-sandbox`. The agent can run any command as you, in its worktree. Use it where you would let an engineer work unattended: a dedicated machine, VM, or container.
- `workspace`: Claude Code `--permission-mode auto`, Codex `--sandbox workspace-write`. Safer, but the Codex sandbox blocks the network by default and may block commits in a worktree, so the agent may not be able to commit, push, or install; the script commits and pushes what it leaves in the worktree.

## State on disk

`ORCHESTRATE_HOME` (default `~/.orchestrate`) holds:

- `agents/<agentId>.json`: the agent's runtime, session id and page, and its runs.
- `runs/<runId>/`: `run.json`, the prompt, `out.log`, `err.log`, and the exit code.
- `worktrees/`, `clones/`, `cache/`: checkouts the runs use. Worktrees of finished local runs stay for inspection; remove them with `git worktree remove` when you no longer need them.

A restarted `run` loop finds every running agent again from these records, so stopping and restarting the loop does not lose running agents. Each run's record is changed under a lock, so a `run` loop, `tail`, and a second planner never settle the same run twice. A settle that fails for a passing reason (a network error, a CLI timeout) is retried on later polls, five times in all; one that cannot work (a diff that no longer applies) makes the run an error. A process is matched by its pid and its start time, so a pid reused after a reboot never counts as the agent.
