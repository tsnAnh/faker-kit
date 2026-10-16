# The event lifecycle worker

A project bot moves each unit through its lifecycle in `team-kit-gh tick`. The bot launches queued units, starts verifiers, opens gates, and merges low-risk units the review app approved. Tick finds that work by polling. The event receiver ([The durable event receiver](event-receiver.md)) keeps each GitHub delivery in a queue. The event worker turns those deliveries into the same lifecycle steps, so a done report, a finished check, or a merged dependency moves the unit without waiting for the next tick.

For the owner, nothing new appears. The worker sends the notices tick would send, starts the agents tick would start, and nothing more. The maintainer inherits one switch, `authority`, that decides which of the two drives the lifecycle, and a log that shows where they would disagree.

## Commands

```bash
team-kit-gh work-events [--sweep]
team-kit-gh authority [show | event | rollback]
team-kit-gh shadow-compare [--hours 24]
```

`work-events` takes this project's due events and runs the lifecycle steps they ask for. It prints notices like tick does, or `[SILENT]`. `--sweep` steps every in-flight unit and lets queued units launch even with no event due.

`authority` shows who drives the lifecycle, sets it to `event`, or rolls it back to `poll`. Like `merge`, it is refused in a turn that read untrusted text.

`shadow-compare` lists the decisions each side logged in the last hours, as matches and mismatches. It reads the log and changes nothing.

## One run, in order

1. **Lock.** The run takes the queue's per-project lock. A second worker for the same project finds it held and ends.
2. **Claim.** It claims every due row of the `lifecycle` consumer for the project, each under a lease with a token, a generation, and an expiry.
3. **Check each row against live GitHub.** A row holds pointers, never text. The worker reads what each pointer names and decides what the row asks for. The table below gives the rules. A row that asks for nothing any more is settled as `superseded` with the reason.
4. **Run the lifecycle once.** `Project.lifecycle` is the code tick runs: the andon cord, then the next step of each in-flight unit (`check_running`, `check_verifying`, `check_app_review`, rework recovery), then launches of queued units. The worker limits which units it steps and whether launches run. Every step reads the issue, its comments, the pull request head, and its checks again.
5. **Settle.** If every step read GitHub, each accepted row is `done`. If a read failed, each accepted row retries with backoff (30 seconds, doubling, up to an hour) and goes to `dead` after five attempts. A not-found pointer ends as `gone`.

| Event | What it asks |
|---|---|
| `issues` | Step that issue's unit if it is in flight, and let queued units launch. A closed dependency, a new unit, or a cleared gate releases work this way. |
| `issue_comment` | Step that issue's unit. A done report, a verdict, or a review result arrives this way. |
| `pull_request`, `pull_request_review`, `pull_request_review_comment` | If the live head differs from the event's head, nothing (superseded). Otherwise step every in-flight unit. A closed pull request also lets queued units launch. |
| `check_run`, `check_suite`, `workflow_run` | If no open pull request is at the event's head, nothing (superseded). Otherwise step every in-flight unit. |

A pull request opening never moves a unit. Only the agent's report comment does, read and checked as tick reads it today.

## One authority

Kit state holds one field per project, `~/.hermes/team-kit/authority/<bot>.json`, with `authority` set to `poll` or `event`. A missing or unreadable file means `poll`, which is how the kit behaved before the worker existed.

- With `poll`, tick acts and the worker is read-only.
- With `event`, the worker acts and tick's lifecycle part is read-only. Tick still publishes unit states, counts units in flight, lands kit pull requests, and reports proposals, pull request comments, and security results, because the worker does none of those.

The read-only side runs the same code. Every side effect crosses one boundary, `Decisions` in `team_kit_gh.py`: a `gh` write, a cloud start, or a notice. The acting side runs each and logs it. The read-only side logs it, runs nothing, and sends no notice. It reads GitHub for real, but its local records go to a scratch copy of the kit state that is deleted after the run. So a verifier record it would have written never reaches the acting side.

Both sides read the authority under one lock, `authority/<bot>.lock`, and hold it for the whole lifecycle run. `team-kit-gh authority` takes the same lock to change it. The authority cannot change during a run, and the two sides never both act.

Only `team-kit-gh authority event` and `team-kit-gh authority rollback` write the field. No event, comment, or title reaches it.

## The shadow comparison

Each decision goes to `~/.hermes/team-kit/events/decisions/<bot>.jsonl` as one line: the time, the side, whether it acted, and the decision as a key. A key names the command, its target, labels, merge method and head, and a comment's first line when that line is a `<!-- team-kit:... -->` marker. Any other comment text is logged as `(text)`. The log never holds what a stranger wrote.

`shadow-compare` groups the keys by side. A decision both sides logged is a match. A decision only one side logged is a mismatch to read, not a failure by itself. The side that ran second may have found the step already done. A unit no event named is stepped only by tick, until the sweep runs.

## Safety

- No model runs in this path. Launch, gate, verify, and merge stay the deterministic code tick runs.
- Event text is never read. The worker uses an event's head only to compare it with the live head, and never to act on. A verdict or an approval counts only for the exact head it names, as before.
- Every gate holds: exact-head CI, risk scoring, verification where configured, the andon cord, the kit pin, `max_in_flight` and `max_in_flight_total`, and same-project and cross-project dependencies. The worker reaches them through the same calls.
- Merge and close by number stay owner or chief requests (`team-kit-gh merge` and `close`). The worker merges only through `auto_merge`, under the same conditions as tick.
- A repeated event, a duplicate delivery, or a run after a crash finds the step already done, because each step reads the live state first. A crash after a side effect and before the settle leaves the row claimed. Its lease expires, the row is claimed again, and the step finds nothing left to do.
- The worker approves nothing and requests no review. Its own tests are not an independent review. The owner's review of a change is the Claude Code `/code-review` the review flow names.

## What stays time-based

Events cannot carry everything, so this design does not reach zero timers.

- **Deadline and silence sweep.** A unit with no report a day after launch, checks pending a day after a done report, or a verifier with no verdict a day after it started sends no event. `team-kit-gh tick` sweeps for them while the authority is `poll`. With `event`, `team-kit-gh work-events --sweep` on a timer does it.
- **Cloud task status.** A Codex task that ends without a report is found by asking `codex cloud status`, in the same sweep.
- **Health and alert sources.** `team-kit-incidents` polls checks, health URLs, and alert sources, because an outage often sends no event.
- **Reconciliation.** A delivery with no usable identifier waits in `reconcile`, and a missed delivery is never queued. A periodic run catches both.
- **Weekly digest and upstream check.** The status digest and `team-kit-gh upstream` run on their schedules.

## Not done here

This unit adds the worker, the switch, and the comparison. It changes no cron job, enables no route, and starts no listener. The authority stays `poll` until an operator runs `team-kit-gh authority event`. Cutover is a later step through the bootstrap workflow, after the shadow comparison shows the worker and tick agree. That step also schedules the sweep and runs `work-events` when a delivery arrives.

## Proven by tests

`hermes/tests/test_event_worker.py` drives the real command against the GitHub double from `test_gh.py`:

- A pull request opening does not mark the unit done.
- A done report with passing checks starts the verifier, with the same writes in the same order as tick.
- Checks completing later advance a unit that waited for them.
- A duplicate event starts no second verifier, merges once, and says each notice once.
- A stale-head event finds no work. An approval of an old head cannot approve the new head.
- A merged dependency releases the queued unit. The andon cord blocks launches.
- A restart after a side effect and before the settle resumes without doing anything twice.
- A failed GitHub read retries and then dead-letters, and a not-found ends as gone.
- With `poll`, the worker only logs and writes no record tick reads. With `event`, tick only logs. Rollback restores `poll`. A garbled authority file means `poll` for both.
- Only the command changes the authority, never after untrusted text.
- `shadow-compare` reports matches and mismatches and leaves every file and GitHub untouched.
