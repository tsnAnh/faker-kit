# The durable event receiver

Hermes bots learn about GitHub mostly by polling: each project bot's `team-kit-tick` job reads labels and reports on a schedule. The owner wants them to run on events instead, as far as that is practical. The first piece is a receiver that can take a GitHub webhook delivery and keep it safely, so nothing it is told is lost between the request and the work. This page explains what that receiver does, what its tests prove, and what it does not do yet.

It is a library (`hermes/lib/team_kit_event_queue.py`) and a command (`hermes/bin/team_kit_events.py`). It runs in shadow mode only, it registers no listener, and it deploys nothing.

## One request, in a fixed order

1. **Verify.** The receiver computes HMAC-SHA256 over the original raw request bytes and compares it to `X-Hub-Signature-256` in constant time. A missing, wrong, or truncated signature answers 401, and nothing from the body is read or recorded. A signature over re-serialized JSON fails, because only the exact bytes GitHub sent count.
2. **Parse and bind.** Only then is the body parsed. `repository.full_name` must be the repository of a project in `bots.yaml`. Any other repository answers 403 and nothing is recorded.
3. **Record and commit.** One row goes into a SQLite database (WAL mode, mode 600, `$TEAM_KIT_HOME/events/events.sqlite3`), keyed by the unique `X-GitHub-Delivery` id, together with one state row for each consumer that takes the event. The transaction commits.
4. **Acknowledge.** Only after the commit does the receiver answer 202. If the insert or the commit fails, it answers 503 so that GitHub redelivers.

Nothing is coalesced, merged, or dropped before the commit, and every distinct delivery id gets its own row. If a delivery id arrives again with the same body digest, the receiver answers 202 and adds no row and no work. If it arrives with a different digest, that is a conflict. The receiver answers 409, keeps the first row, and counts the conflict where `status` shows it.

## A row holds pointers, never text

A row keeps the delivery id, the repository's full name and numeric id, the event, the action, the sender's login, a SHA-256 digest of the raw body, and the time the delivery arrived. It also keeps the typed identifiers the event carries. Each one is a column of its own, set only for events that have it.

| Event | Identifiers kept |
|---|---|
| `pull_request` | number, head sha, base sha, base ref, head repository name and id |
| `pull_request_review` | the same, plus the review id |
| `pull_request_review_comment` | the same, plus the comment id |
| `issues` | issue number |
| `issue_comment` | issue number and comment id, which is how a worker's report comment is found again |
| `check_suite` | check-suite id, head sha |
| `check_run` | check-run id, check-suite id, head sha |
| `workflow_run` | workflow-run id, head sha, head repository name and id |
| `push` | head sha (`after`), base sha (`before`), ref |

No payload body, no issue or comment text, and no secret is stored. Every event can still be found again and replayed after a restart from its identifiers. An event with no usable identifier is recorded anyway, marked for repository-level reconciliation, and never counted as consumed.

Each row also records `shadow_check`, the job its event would trigger today (`team-kit-tick` or `team-kit-security`, from the routes in `hermes/lib/team_kit_machine.py`, or `none`).

## Consumers

Each consumer has its own state row for each delivery. The lifecycle worker takes the events tick takes, plus `pull_request_review_comment`, `check_run`, and `workflow_run`. The security job takes `push`. So one consumer finishing never hides a delivery from another.

A row moves through these states:

| State | Meaning |
|---|---|
| `pending` | Waiting for its consumer |
| `claimed` | A worker holds a lease on it |
| `retrying` | A read failed. It is due again at its stored `next_at` |
| `done` | Handled |
| `superseded` | The object moved on (a new head, or a gate no longer applies). Not success |
| `gone` | Confirmed not found after the bounded attempts. Not success |
| `dead` | Failed for good, or out of attempts. Never deleted automatically |
| `reconcile` | The event carries no identifier. It waits for a repository-level reconciliation, and no worker claims it |

A claim belongs to one consumer and returns a lease: a random token, a generation number, and an expiry. `complete`, `fail`, and `retry` must present the current token and generation before the expiry, or they are refused. A worker whose lease expired and whose row was claimed again therefore can never settle the newer claim. A lease that expires without a settle (a crash) sends the row back to `pending` under a new generation, so the row is not lost. Retries back off from 30 seconds, doubling up to a cap of an hour, and end in `dead` (or `gone` for a confirmed not-found) after five attempts. The clock is always a parameter. A per-project lock with an expiry keeps two workers off one project.

`handle_next` is the contract a consumer follows. The stored identifiers are only pointers. The consumer re-fetches the objects they name through a trusted read (an injected fetcher), checks the repository, number, and head against the live data and the current gates, and never treats stored or fetched text as instructions. A transient read failure retries with backoff. A confirmed not-found ends as `gone`. A moved head ends as `superseded`. A live object in another repository is a dead letter.

## What "once" means here

Each delivery is recorded durably once and handled logically once per consumer. That is not exactly-once for side effects on GitHub. A crash after a side effect and before `complete` handles the row again. So every transition must check the live state first and skip what already happened: `handle_next` asks `Transition.done` before it calls `apply`.

## Commands

```bash
python3 hermes/bin/team_kit_events.py receive --request request.http --secret-env TEAM_KIT_WEBHOOK_SECRET
python3 hermes/bin/team_kit_events.py status [--project shop]
python3 hermes/bin/team_kit_events.py dead-letters [--project shop]
```

`receive` reads one HTTP request from a file or stdin (`-`): header lines, a blank line, then the raw body bytes. It prints the answer it would give GitHub as JSON. It reads the signing secret from the environment variable or the file you name (`--secret-env NAME` or `--secret-file PATH`), never from an argument value. `status` and `dead-letters` open the database read-only and print no secret and no lease token.

## Safety

- No model runs in this path, and the receiver reads no Hermes database, no default profile, and no secret store.
- Event text is untrusted data. At most it hints that a deterministic check should run. It is never owner authorization, and it never chooses a merge, a launch, or a close.
- Shadow mode is the only mode. The receiver changes no unit label, launches nothing, merges nothing, and closes nothing. `team-kit-tick` stays authoritative.

## Proven by tests

`hermes/tests/test_event_queue.py` proves each of these against the real code:

- A valid signature answers 202 only after the commit. A failing commit, injected, answers 503 and leaves no row. An insert failure also answers 503.
- A wrong, missing, truncated, or body-modified signature answers 401 and stores nothing. A signature over re-serialized JSON is rejected. A repository outside `bots.yaml` is refused and stores nothing.
- A duplicate with the same digest leaves one row and answers 202. A duplicate with a different digest is a 409 conflict that keeps the first row and is counted. Dedupe holds across new store instances and across separate processes of the command.
- For `pull_request`, `issue_comment`, `check_suite`, `check_run`, `pull_request_review`, `workflow_run`, and `issues`, the typed identifiers are read back after a restart, and no issue or comment text is in any table or database file. A report comment is found again from its stored issue number and comment id. An event with no identifier is recorded for reconciliation and not consumed.
- Two consumers each claim a delivery once. Retries back off on an injected clock and end in `dead`. The project lock serializes workers.
- A stale token or an old generation cannot complete, fail, or retry a newer claim. An expired lease returns the row to `pending` with the generation advanced, and it is claimed again. Two consumers fence independently.
- With an injected fetcher, a transient failure retries, a confirmed not-found ends `gone`, and a moved head ends `superseded`, and none of these is recorded as done. A crash after a side effect and before `complete` is handled again with no second effect.
- The command never prints the secret, never puts it in argv, and calls no `gh`, `hermes`, `git`, `claude`, or `codex`. `status` and `dead-letters` leave the database unchanged and print no lease token.

## Not verified against Hermes 0.21.7

What follows was read from the public upstream Hermes main branch. None of it was checked against the Hermes 0.21.7 installed on the owner's machine. Treat every point as version-unverified.

- Normal LLM routes write a durable `session_admissions` record before their 202, answer 503 on an admission error, and dedupe persistently on profile, route, and delivery id.
- A `--script` route receives only the payload, without the delivery headers. A script failure or timeout still answers 200 and is ignored, so such a route cannot be a durable ingress.
- A coalesced 202 happens before persistent admission. A cron-trigger 202 happens before the background work runs.
- Deliver-only chat is not a durable queue.
- There is no supported, deterministic API for a worker to claim from the upstream ledger, so nothing here assumes a consumer can read it.

What was observed on the owner's machine is that Hermes 0.21.7 is installed, that port 8644 is a shared default-profile multiplexer serving profile-bound `/p/<bot>/webhooks` routes, and that the repository has no repository webhooks configured.

## Limits

- A webhook cannot detect an outage that produces no GitHub event. A check that stalls, a runner that never starts, or a health URL that fails sends nothing, so polling (`team-kit-tick`, `team-kit-incidents`) stays necessary.
- This unit registers no listener, enables no route, changes no tunnel, and edits no cron job.
- Wiring the receiver behind a Hermes route or the tunnel is a separate later unit, done through the bootstrap workflow. So is any cutover that lets the queue drive work instead of `team-kit-tick`.
