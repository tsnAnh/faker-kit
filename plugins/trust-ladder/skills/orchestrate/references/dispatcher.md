Operating manual for the dispatcher. If you were started with a prompt beginning "You are the root planner for:" or "You are a subplanner for:", read `planner.md` instead.

# Dispatcher

The dispatcher is one-shot. Take the user's goal, start a root planner, return what it prints, stop.

## The job

1. Take the user's goal. Ask for clarification only if the goal is missing or ambiguous. The user chose parallel orchestration deliberately; push back only if the task is genuinely trivial, and point them to a single agent instead.
2. Settle where the agents run, from what the user said or by asking once:
   - `--backend claude` (default) or `--backend codex`: which agent every node uses. Planners can still put single tasks on the other one.
   - `--runtime cloud` (default) or `--runtime local`: where workers and verifiers run. Cloud scales past this machine; local needs no cloud environment and shows every event.
   - For Codex in the cloud, the repository's Codex cloud environment id, `--codex-env <id>`.
3. From a clone of the repository, run the kickoff CLI with the goal and those choices.
4. Return the identifiers it prints and how to watch the run. Stop. The planner self-drives.

One-time setup: run `bun install` inside this skill's `scripts/` directory if `node_modules/` is missing.

```bash
bun <skill>/scripts/cli.ts kickoff "<goal>" [--backend claude|codex] [--runtime cloud|local] [--codex-env <id>] \
  [--repo <url>] [--ref <branch>] [--model <slug>] [--local-sandbox full|workspace] \
  [--slack-channel C123] [--dispatcher-name "Alex"]
```

Kickoff resolves the repository from `git config --get remote.origin.url` unless `--repo` is given, creates the branch `orch/<rootSlug>/root` in a fresh worktree under `ORCHESTRATE_HOME`, and starts the root planner there as a detached `claude -p` or `codex exec` process. It prints `{ agentId, runId, status, branch, worktree, workspace, log }` JSON. The root slug comes from a `slug:` prefix on the goal, or from its first line. Kickoff adopts a root planner for the same slug that started in the last 30 minutes and is still running, unless `--force` is given.

`--local-sandbox` sets what local processes may do. `full` (default) runs them without approvals or a sandbox, so they can run tests, install packages, and push. `workspace` uses Claude Code's auto mode and Codex's workspace-write sandbox, which can block pushes and network access. Use `full` on a machine or container you would let an engineer work in unattended.

## Planning here instead

If the user wants to watch or steer the plan, skip kickoff and act as the root planner in this session: read `planner.md`, write `plan.json` in `.orchestrate/<rootSlug>/` of a checkout on its own branch, and drive it with `run --root`. Everything else is the same.

## Dispatcher identity

Kickoff bot username is `<firstName>'s bot` when the first name resolves, otherwise `orchestrate`. Resolution order:

1. `--dispatcher-name "Alex"` flag.
2. Slack `users.lookupByEmail` against `git config user.email` (best-effort; missing scope or no match leaves it unset).

The CLI passes the resolved name to the root planner via the kickoff prompt; the planner writes `plan.dispatcher = { firstName: "<name>" }` into `plan.json`. Child tasks keep their own task name as bot identity.

## Run summary

The root planner writes `plan.summary` as a one-line orientation for the human in the Slack thread. Kickoff posts `<rootSlug>: <summary> <agent-link>`; without `summary` it truncates `goal` to ~200 chars. `summary` is for the human; `goal` stays as the agent-facing full text.

## Minimal-goal discipline

Pass the user's goal through without expanding it. Don't add planning heuristics, subplanner counts, or structural prescriptions. The planner reads this skill and decides its own decomposition. Over-prescribing leaks dispatcher context into the planner's window.

## Observability after kickoff

- `bun <skill>/scripts/cli.ts tree <workspace>` and `status <workspace>` for the task tree, where `<workspace>` is the `workspace` kickoff printed.
- The root planner's own output: the `log` file kickoff printed.
- `bun <skill>/scripts/cli.ts crawl <repo-path> orch/<rootSlug>/root <rootSlug>` for a deep tree view across subplanner branches.
- The Slack kickoff thread in `plan.slackChannel`, when `SLACK_BOT_TOKEN` is set.
- Cloud sessions and tasks also show on claude.ai/code and chatgpt.com/codex.

`syncStateToGit` defaults to true, so state and handoffs are committed to the planner's branch and pushed. Set `syncStateToGit: false` on the root plan when goals or handoffs should not be committed.
