# The durable event receiver

Hermes bots learn about GitHub mostly by polling: each project bot's `team-kit-tick` job reads labels and reports on a schedule. The owner wants them to run on events instead, as far as that is practical. The first piece is a receiver that can take a GitHub webhook delivery and keep it safely, so nothing it is told is lost between the request and the work. This page explains what that receiver does, what its tests prove, and what it does not do yet.

It is a library (`hermes/lib/team_kit_event_queue.py`), a loopback HTTP ingress (`hermes/lib/team_kit_ingress.py`), and a command (`hermes/bin/team_kit_events.py`). It runs in shadow mode only. The ingress listens on a loopback address only, and nothing here deploys it: no public listener, no tunnel, no registered webhook, and no installed service. The system is not event-driven end to end yet; see [What bootstrap must still do](#what-bootstrap-must-still-do).

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

## The loopback ingress

`team-kit-events serve` is the piece that accepts a real HTTP request. It is the Python standard library's threading HTTP server, with no framework and no new dependency. For each request it reads the body exactly as sent, as bytes, and hands those bytes and the original headers to the receiver above. It writes the receiver's answer back as the HTTP status with a small JSON body: `{"status": 202, "reason": "recorded", "delivery": "<id>"}`. The success answer is written only after the receiver returns, which is after the commit. If the queue cannot be opened or the commit fails, the answer is 503, so GitHub redelivers.

It binds to a loopback address only (127.0.0.0/8 or `::1`, written as a literal address). It refuses to start on any other address, `0.0.0.0` and `localhost` included, and says why. Something else must put HTTPS in front of it.

### Routes

The operator writes one JSON configuration file. One process serves every project from it: one route per project, each with its own path, repository, and secret, so the registered projects need not share a key and one process is all the owner runs.

```json
{
  "bind": "127.0.0.1",
  "port": 8650,
  "routes": [
    {"path": "/github/shop", "repository": "acme/shop", "secret_file": "secrets/shop.secret"},
    {"path": "/github/crm", "repository": "acme/crm", "secret_file": "secrets/crm.secret"}
  ],
  "limits": {"max_body": 1048576}
}
```

- `secret_file` is a path to the file holding that route's webhook secret, never the secret itself. A relative path resolves against the configuration file's folder.
- `repository` must be a project in `bots.yaml`. The route binds to it: the receiver is given only that one repository as a known project, so a correctly signed payload that names any other repository is refused with 403 and nothing is written. That is what stops one project's route from recording another project's events.
- A request to a path with no route answers 404.

Startup fails closed on any configuration problem, and lists all of them: a non-loopback bind, a missing port, no routes, a duplicate path or repository, a route with no repository or one that is not a project in `bots.yaml`, an unknown key, and a secret file that is missing, unreadable, empty, or group or world accessible. A message names the file and its mode, never the secret.

The secret file is read again for every request, with the same checks. Rotating a secret means rewriting its file, with no restart. If the file has become unreadable or too open, the request answers 503 and nothing is recorded.

### Limits

| Limit | Value | Answer when exceeded |
|---|---|---|
| Body size | 1 MiB (`max_body`). GitHub caps a payload at 25 MiB, but the events the kit takes are far smaller, and a smaller cap bounds memory per connection. | 413, before any body byte is read |
| `Content-Length` | Required, digits only, given once. No `Transfer-Encoding`, so no chunked bodies | 411 when missing or chunked, 400 when malformed or repeated |
| Request line and headers | 16 KiB together (`max_header_bytes`), at most 64 headers (`max_headers`) | 431 |
| Time | 10 seconds (`read_timeout`), both for each read and for the whole request | 408, and the connection is closed |
| Connections | 16 handled at once (`max_connections`), each on its own thread | 503 with `Retry-After` |

Each connection carries one request and is closed after the answer. A slow client holds only its own thread, until the timeout. SQLite serializes the writes, through the store's immediate transactions. Any other method than POST answers 405 with `Allow: POST`.

### Logs

One line per answered request, on stderr: the time, the route's project, the status, the delivery id, and the body length. Headers, the body, the signature, and the secret are never logged, and no answer echoes request input.

## Commands

```bash
python3 hermes/bin/team_kit_events.py receive --request request.http --secret-env TEAM_KIT_WEBHOOK_SECRET
python3 hermes/bin/team_kit_events.py status [--project shop]
python3 hermes/bin/team_kit_events.py dead-letters [--project shop]
python3 hermes/bin/team_kit_events.py check-config --config ingress.json
python3 hermes/bin/team_kit_events.py serve --config ingress.json [--db PATH]
```

`receive` reads one HTTP request from a file or stdin (`-`): header lines, a blank line, then the raw body bytes. It prints the answer it would give GitHub as JSON. It reads the signing secret from the environment variable or the file you name (`--secret-env NAME` or `--secret-file PATH`), never from an argument value. `status` and `dead-letters` open the database read-only and print no secret and no lease token. `check-config` validates the ingress configuration and every secret file's permissions without printing a secret, and exits non-zero on any problem. `serve` runs the ingress in the foreground until it is stopped.

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

`hermes/tests/test_ingress.py` proves the ingress over real sockets on 127.0.0.1, with an ephemeral port and a temporary database:

- A signed request answers 202, and a second database connection sees the row the moment the 202 arrives. A failing commit answers 503 and leaves no row. A queue that cannot be opened answers 503.
- A duplicate answers 202 and stays one row. The same delivery id with another body answers 409 and keeps the first. A wrong or missing signature answers 401 and records nothing. The signature is checked over the exact bytes sent: a body with unusual whitespace and key order verifies, and one changed byte does not.
- A payload for another repository on a route answers 403 and records nothing. Two routes with different secrets do not accept each other's signatures. A rotated secret file takes effect on the next request.
- An unknown path answers 404, and GET, PUT, and DELETE answer 405. An oversize body answers 413 without being sent. A missing, malformed, or chunked length is refused, and a length longer than the body times out. Too many or too large headers answer 431. A slow client is dropped at the timeout while another request is served, and a connection over the limit answers 503.
- Startup and `check-config` refuse a non-loopback bind, a group- or world-readable secret file, a missing or empty secret file, a duplicate path, a route with no repository, and a repository outside `bots.yaml`.
- A restarted process on the same database keeps earlier deliveries and still dedupes. No log line, stdout, or stderr holds a secret or the payload.

## Not verified against Hermes 0.21.7

What follows was read from the public upstream Hermes main branch. None of it was checked against the Hermes 0.21.7 installed on the owner's machine. Treat every point as version-unverified.

- Normal LLM routes write a durable `session_admissions` record before their 202, answer 503 on an admission error, and dedupe persistently on profile, route, and delivery id.
- A `--script` route receives only the payload, without the delivery headers. A script failure or timeout still answers 200 and is ignored, so such a route cannot be a durable ingress.
- A coalesced 202 happens before persistent admission. A cron-trigger 202 happens before the background work runs.
- Deliver-only chat is not a durable queue.
- There is no supported, deterministic API for a worker to claim from the upstream ledger, so nothing here assumes a consumer can read it.

What was observed on the owner's machine is that Hermes 0.21.7 is installed, that port 8644 is a shared default-profile multiplexer serving profile-bound `/p/<bot>/webhooks` routes, and that the repository has no repository webhooks configured.

## What bootstrap must still do

The ingress is built and tested; nothing is deployed. Before any GitHub delivery reaches it, the bootstrap bot (or the owner) must:

1. Create one secret file per project, mode 600 and owned by the user that runs the ingress, and write the routes file. Run `check-config` until it passes.
2. Run `serve` as a long-running process on a loopback address, and put HTTPS in front of it (the existing tunnel or a reverse proxy), forwarding each route's path unchanged.
3. Register a repository webhook for each project, with content type `application/json`, the route's public URL, and the same secret as its file.
4. Verify with a real delivery while `team-kit-tick` is not running, by reading the row back with `status`, then redeliver it from GitHub and see one row with a 202.

Until all of that is done and a consumer drives work from the queue, the bots stay poll-driven.

## Limits

- A webhook cannot detect an outage that produces no GitHub event. A check that stalls, a runner that never starts, or a health URL that fails sends nothing, so polling (`team-kit-tick`, `team-kit-incidents`) stays necessary.
- Nothing here installs a service, binds a public address, opens a firewall, changes a tunnel, registers a webhook, or edits a cron job.
- Deploying the ingress behind HTTPS is a separate later unit, done through the bootstrap workflow. So is any cutover that lets the queue drive work instead of `team-kit-tick`.
