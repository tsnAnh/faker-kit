Loop hygiene:
- Run `{{cli}} run{{rootFlag}} --max-runtime-sec 480 <workspace>` in the foreground, with a command timeout of at least 10 minutes (Claude Code: the Bash tool's `timeout: 600000`). Never background it; the loop is the heartbeat.
- Exit code 100 is a planned checkpoint restart, not an error. Rerun the same command immediately; it resumes from `state.json` and reattaches to every running agent.
- Exit code 1 on a non-empty error set is your turn. The loop exited because a task crashed; the script already wrote a synthetic `handoffs/<task>-failure.md` for each dead worker and any `handoffs/<task>-finished-no-handoff.md` for workers that ended without a structured handoff. In-flight workers keep running; the next `run` reattaches via `recoverRunning`.
- After `run` returns, call `{{cli}} tree <workspace>`. If any task is still `pending` or `running`, loop again.
- Don't end your turn while this workspace has non-terminal tasks.

Reacting to failure handoffs:
For each task with `status: "error"` and a matching `handoffs/<task>-failure.md`, read the `Failure mode` line and decide:
- `cap-hit` or `oom`: retry with smaller scope (split into narrower tasks, tighter `pathsAllowed`, leaner `scopedGoal`).
- `network-drop`: retry as-is; treat as transient.
- `tool-error`: retry with a different `model`.
- `unknown`: read the `Last activity` and `Error` lines; if no signal, treat as transient and retry as-is; abandon if it fails again.
For `<task>-finished-no-handoff.md`, read the raw snippet at `handoffs/<task>.md` and decide whether the worker's intent was recoverable; retry or abandon.
Each retry costs another agent run; budget your decisions. After 2 retries on the same task, prefer abandon (drop the task from `plan.json`, replan around it) over a 3rd attempt unless you have specific evidence the next retry will succeed. Update `plan.json`, then re-run `{{cli}} run{{rootFlag}} --max-runtime-sec 480 <workspace>` to continue.
