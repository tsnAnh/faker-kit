# Moving Hermes from polling to events

This page is the inventory and design for moving the Hermes installation from scheduled and polling jobs to event-driven operation. It changes no behavior. Later units implement it: the event receiver is [#156](https://github.com/tsnAnh/faker-kit/issues/156) and the lifecycle worker is [#157](https://github.com/tsnAnh/faker-kit/issues/157). Every inventory row traces to a file in this repository.

Each statement on this page is one of three kinds, and says which:

- **Observed.** Read from this repository's code, or from the installed Hermes 0.21.7 on the Hermes machine.
- **Desired.** The target architecture. Nothing labelled desired is implemented.
- **Not verified against 0.21.7.** Read from the public upstream Hermes main branch only. It may differ in the installed version.

Facts that only the Hermes machine can show and that no reading has settled yet are marked **Pi**, with the exact read-only commands in [Hermes capabilities](#hermes-capabilities).

## What already exists

**Observed, in the code.** `webhooks` in the machine config (`hermes/lib/team_kit_machine.py`, `hermes/bin/team_kit_admin.py` `webhooks()`) can give each project bot two signed Hermes webhook routes on `127.0.0.1:8644`, published through a Cloudflare Tunnel:

| Route | GitHub events | Fires |
|---|---|---|
| `<bot>-tick` | `issues`, `issue_comment`, `pull_request`, `pull_request_review`, `check_suite` | the `team-kit-tick` no-agent job |
| `<bot>-security` | `push` | the `team-kit-security` no-agent job |

`ROUTE_JOBS` in `team_kit_machine.py` is the list. The schedule stays as the fallback for a missed delivery. `setup-machine.sh` step 7 sends one signed test event per bot and requires a 202 before it finishes.

**Observed, on the installation.** This repository has no repository webhooks configured, so no GitHub event reaches Hermes today and every trigger below runs on its schedule. `cloudflared` is running, its persistence across a reboot is unverified, and no systemd unit for it was found.

What the code path lacks:

- No durable queue. A route fires a cron job directly.
- No delivery-id dedupe and no per-event record. Idempotence comes from `tick` re-reading GitHub each run.
- No dead-letter view. A failed run surfaces only as a cron failure notice.
- Event coverage gaps. `pull_request_review_comment`, `check_run`, `workflow_run` and commit `status` are not subscribed. The default branch's checks (incidents) have no route at all.
- `tick` takes no input from the event. It re-reads everything every time, so one event costs a full tick.

## Principle for the migration

An event is a **hint to run a deterministic check**, never an instruction and never authorization. The worker re-reads GitHub for current state, so a duplicate, a stale or a reordered event is harmless, and a missed event is repaired by reconciliation. This keeps the Hermes invariants in the root AGENTS.md. Code decides what launches and gates, and no model chooses. External event text never becomes owner authorization, and never leads to a model-chosen merge, launch or close.

## Inventory

Columns: id, owner profile, mechanism and interval, source, what it reads or does, event available, proposed trigger, residual timer, LLM use, migration unit. "Unit" names a unit this design proposes. The owner sequences them.

Default intervals are from `hermes/plugin/team_kit_guard/policy.py` (`DEFAULT_TICK_EVERY` 30m, `DEFAULT_INCIDENTS_EVERY` 10m, `DEFAULT_SECURITY_EVERY` 15m). The machine config template sets `tick_every: 10m` (`hermes/templates/machine.yaml`). The live values are **Pi** (`hermes -p <bot> cron list --all`).

### Hermes cron jobs, project bots

Source for all rows: `hermes/cron/project-jobs.md`, `hermes/bin/team_kit_admin.py` (`ensure_jobs`), `hermes/bin/team_kit_gh.py`. LLM use is none for every job (`--no-agent`).

| Id | Mechanism | What it does | Event available | Proposed trigger | Residual timer | Unit |
|---|---|---|---|---|---|---|
| T1 `team-kit-tick` | cron `every 30m` default, plus webhook route `<bot>-tick` | The umbrella run. Sub-triggers T1a to T1l below | Yes, for most parts | The lifecycle worker runs the check each queued event names | Yes, see R1, R2, R4 | U3 |
| T2 `team-kit-status` | cron `0 9 * * 1` (Asia/Saigon) | Weekly digest of units in flight, waiting and done (`Project.status`, marker file `status/<bot>.json`) | No, a digest is time-defined | None | Yes, R5 | U6 |
| T3 `team-kit-security` | cron `every 15m` plus webhook route `<bot>-security` on `push` | Starts one cloud security review when the default branch moved and none is running (`Security`) | Yes, `push` to the default branch | Push event, filtered to the default ref, enqueues the job | No for the start. The 24h no-result deadline is R1 | U4 |
| T4 `team-kit-incidents` | cron `every 10m`, no route | Default branch checks, health URLs, external alert sources, triage session, auto close (`Incidents`) | Default branch checks: yes (`check_suite`, `workflow_run`). Health URLs: no. External alerts: not from GitHub | Subscribe `check_suite`/`workflow_run` for the default branch. Health and alert sources stay polled | Yes, R3 | U5 |
| T5 `team-kit-upstream` | cron `0 9 * * 1` | Compares the upstream repo head with `upstream/source.json` and dispatches one sync unit (`Upstream`) | No. The upstream repository is not owned here, so no webhook can be added | None | Yes, R6 | U6 |

`team-kit-gh tick` internals, each its own trigger because each has its own event or none. File: `hermes/bin/team_kit_gh.py`, `Project.tick` and the methods named.

| Id | Detects | Event available | Deterministic decision | Residual timer |
|---|---|---|---|---|
| T1a andon sync (`sync_andon`) | Open `agent:andon` issue by a trusted login; cord cleared | `issues` (opened, labeled, closed, unlabeled) | Raise or clear the local cord from the trusted-author list | No |
| T1b teammate proposals (`proposal_notices`) | New `agent:proposal` issue | `issues` labeled | Collaborator check, one notice per proposal | No |
| T1c PR comment mentions (`pr_comment_notices`) | A collaborator's PR comment that mentions the bot | `issue_comment` (subscribed). `pull_request_review_comment` is not | Notice with link, never the text. The record `since` time stays as the reconciliation anchor | No, once the event is added |
| T1d kit PRs (`kit_prs`) | Kit vendoring pull requests that match the local kit commit | `pull_request`, `push` | Compare PR head to the local commit, notify once | No |
| T1e unit-state publish (`publish`, `UnitStates`, `~/.hermes/team-kit/units/<bot>.json`) | Closed or changed units that another project bot's briefs depend on | The closing event happens in the other bot's repository, so this bot's webhook never sees it | Needs a local event: when bot A publishes, wake the bots whose `wants` list names A | Yes, R4 as the fallback |
| T1f running units (`check_running`) | A signed done report comment; a stopped cloud task | Report comment: `issue_comment`. Cloud task end (`codex cloud status`): no event | Parse the signed report, gate or advance | Yes, R1 (24h no-report deadline) and R2 (cloud task status) |
| T1g verifying units (`check_verifying`) | Verifier verdict comment, PR head change | `issue_comment`, `pull_request` (synchronize) | Exact-head verdict, risk score, optional auto merge | Yes, R1 (verifier deadline) |
| T1h review and auto-merge retries (`check_app_review`, `auto_merge`) | Review app verdict on the exact head; a merge GitHub refused earlier; cord cleared | `pull_request_review`, `check_suite`, andon `issues` event | Merge only when risk is low, checks passed, head matches (`--match-head-commit`) | No for the verdict. A retry after a transient refusal needs R4 |
| T1i pending checks (`check_running` tail) | Checks passed or failed on the reported head; 1 hour no-checks grace; 24 hour pending deadline | `check_suite` completed | Gate on failure, advance on pass | Yes, R1 (`NO_CHECKS_GRACE_SECONDS`, `REPORT_DEADLINE_SECONDS`) |
| T1j launch (`launch`) | A queued unit whose dependencies merged and a free slot | `issues` closed (dependency), label changes (slot freed), local `units/` change for cross-project | Dependencies, `max_in_flight`, andon, kit pin checks, all in code | No for same-project. Cross-project is T1e |
| T1k security check inside tick (`Security.check`) | Signed security result comment; stopped task; 24h no-result | `issue_comment` on the `agent:security` issue | Verify the HMAC signature, notify only on high or medium | Yes, R1, R2 |
| T1l in-flight counts (`InFlight`, `IN_FLIGHT_FRESH_SECONDS`, `UNIT_STATE_FRESH_SECONDS`, both 24h) | Staleness of the shared counts and unit states other bots read | No | A stale record stops counting after 24 hours | Hidden dependency: with no tick, counts silently expire. R4 refreshes them |

### Hermes cron jobs, bootstrap bot

| Id | Mechanism | What it does | Event available | Proposed trigger | Residual timer | Unit |
|---|---|---|---|---|---|---|
| T6 `team-kit-inbox` | cron `every 30m`, no agent. Source `hermes/cron/bootstrap-jobs.md`, `team_kit_admin.py` `inbox` | Delivers one waiting chief project request as a notice | Yes, a local one. `team-kit-chief new-project` writes a request file (`lib/team_kit_requests.py`) and already wakes the job with `hermes cron run` (`wake_job` in `team_kit_chief.py`) | Keep the wake as the primary path | Yes, R7, the 30 minute fallback on the bootstrap bot | U2 |

### Chief profile

`hermes/profiles/chief/config.yaml` defines no schedule, and `ROLE_JOBS` in `team_kit_admin.py` creates jobs only for the `project` and `bootstrap` roles. The chief has no cron job in this repository. **Pi:** `hermes -p chief cron list --all` shows whether one was added by hand. The default profile's jobs need the same check. This row is final only after that read.

### Machine services

Source: `hermes/setup-machine.sh`, `hermes/lib/team_kit_machine.py`, `hermes/bin/team_kit_runner.py`, `hermes/lib/team_kit_slack_relay.py`.

| Id | Mechanism | What it does | Event source | Timer content | Proposed | Unit |
|---|---|---|---|---|---|---|
| T7 Hermes gateway | launchd/systemd service from `hermes gateway install` | Serves every profile, the webhook listener and cron | n/a | Supervisor restart only | Unchanged | none |
| T8 Cloudflare Tunnel | `service_user` unit in the code, `Restart=always RestartSec=10`. Observed on the installation: `cloudflared` runs, with no systemd unit found | Publishes only `/p/<bot>/webhooks/<route>` | n/a | Supervisor restart, if a unit exists | Unchanged. The public listener stays off until HTTPS and the domain are verified | U1 |
| T9 CI runners `team-kit-ci-<repo>-<slot>` | `Restart=always RestartSec=10`, loop in `Slot.forever` with `BACKOFF_SECONDS = (30, 60, 120, 300)` | Registers a just-in-time runner, waits for one job, runs a container, removes it | GitHub assigns the job to the registered runner, so it is event-driven already. The loop is a supervisor with backoff | Backoff after failure only | Unchanged | none |
| T10 Slack relay | `team-kit-slack run`, Socket Mode over an outbound WebSocket, reconnect backoff up to 300s (`SlackRelay.run`) | Relays app mentions into Bot Chat; mirrors notices out | Slack Socket Mode events, outbound only | Reconnect backoff | Unchanged. Its event ids are the dedupe model reused below (`event_keys`, `seen`) | none |
| T11 Notice delivery | Hermes `bot-chat:<bot>` delivery of each job's output; `mirror_notice` | Shows each notice | The job output itself | None | Unchanged | none |
| T12 `setup-machine.sh`, `install.sh` | Run by hand | `sleep` loops wait for the listener and tunnel during setup only | n/a | Setup waits, not runtime triggers | Unchanged | none |

Repository workflows: `.github/workflows/` defines no `schedule:` trigger. `docs-site.yml` and the review workflow run on push and pull request events already.

Not found in this repository: backup jobs, log rotation, update jobs, or any maintenance timer. **Pi:** the commands below list anything added by hand.

## Residual timers

These triggers have no external event or need a deadline, so they stay timers in the desired design. The migration does not reach zero timers.

| Id | Timer | Minimal design (desired) | What is lost if removed |
|---|---|---|---|
| R1 | Deadline sweep for silence: no agent report in 24h, checks pending 24h, verifier, review, security and triage with no signed result in 24h, the 1 hour no-checks grace | One timer per project bot, hourly proposed. It runs only the deadline checks, read-only except gating. Alternative: schedule a one-shot per unit at launch; the hourly sweep is simpler and survives restarts | A session that dies silently would hold its slot forever. Silence produces no GitHub event |
| R2 | Cloud task status (`codex cloud status`) | Poll only while at least one Codex task is running, every 5 minutes. No timer when none runs | A finished task with no comment would wait for the 24h deadline instead of being reported in minutes |
| R3 | Health URLs and external alert sources (Datadog, Sentry, Grafana, PagerDuty in `lib/team_kit_incident_sources.py`) | Outbound poll every 5 to 10 minutes, only when `.agents/dispatch.json` configures a target. Pull-only keeps the outbound-first rule | A target that goes down with no deploy would never open an incident |
| R4 | Reconciliation: a missed or dropped delivery, listener downtime, a GitHub outage, the cross-project unit-state change (T1e), merge retries (T1h), and the 24h staleness of T1l | One timer per project bot, hourly proposed, plus one run on listener start and on gateway restart. It runs only the targeted checks listed here and a GitHub deliveries cross-check (`GET /repos/{owner}/{repo}/hooks/{id}/deliveries`, then redelivery). A full `tick` every hour is not the target | Any single lost event would leave a unit stuck until a human noticed |
| R5 | Weekly status digest (T2) | Keep `0 9 * * 1` | The digest. No event describes "a week passed" |
| R6 | Weekly upstream sync check (T5) | Keep `0 9 * * 1`. An upstream release webhook would need ownership of that repository | New upstream versions would go unnoticed |
| R7 | Bootstrap inbox fallback (T6) | Keep a fallback on the bootstrap bot behind the existing local wake | A request whose wake failed would wait until a human noticed |

### Count by kind

The count is not one number. It depends on the bot's role and its `.agents/dispatch.json`.

| Kind | Timers | Where | Runs |
|---|---|---|---|
| Deadline (R1) | 1 | each project bot | always |
| Reconciliation (R4) | 1 | each project bot | always |
| Calendar (R5) | 1 | each project bot | weekly |
| Calendar (R6) | 0 or 1 | a project bot whose repository sets `upstream_sync: true` | weekly |
| Conditional poll (R2) | 0 or 1 | each project bot | only while a Codex cloud task runs |
| Outbound poll (R3) | 0 or 1 | each project bot | only when a health or alert target is configured |
| Local-wake fallback (R7) | 1 | the bootstrap bot | always |

So a project bot keeps three always-on timers (R1, R4, R5), plus R6 on the kit repository, plus R2 and R3 only while they have work. The bootstrap bot keeps one. Process supervisors (T7 to T10) restart a dead service and are not job triggers, so they are not counted.

R1 and R4 may share one hourly timer per project bot, which takes the always-on count to two. That is allowed only if the lifecycle worker's implementation and its tests prove the shared timer covers every R1 deadline and every R4 repair. Until then, the count above stands. Whether to share, and every cadence, are owner decisions 1 and 2.

## Hermes capabilities

**Observed on the installed Hermes 0.21.7.**

- The webhook platform supports route-profile, cron-job, script and deliver-only routes, each with a route HMAC secret.
- Port 8644 is a shared listener in the default profile that multiplexes profile-bound `/p/<bot>/webhooks` routes.
- `cloudflared` is running. Its persistence is unverified, and no systemd unit was found.
- This repository has no repository webhooks configured.

**Not verified against 0.21.7.** Read from the public upstream Hermes main branch only:

- Durable session admission before the 202 response.
- A `--script` route receives the payload but no delivery headers, and returns 200 when the script fails.
- For coalesced routes and cron-trigger routes, the 202 is sent before persistent admission.

Because of the last two, the desired receiver (#156) must not rely on a Hermes `--script` or cron-trigger route to acknowledge GitHub. It acknowledges only after its own commit, as the pipeline design below specifies.

**Recorded in this repository**, with the file that records each:

| Capability | Recorded in | Status |
|---|---|---|
| `platforms.webhook` with `enabled`, `extra.host`, `extra.port`, `extra.routes` | `team_kit_admin.py` `webhooks()` writes these keys; `hermes/README.md` lists them | Written by this repository's code |
| Route fields `events`, `secret`, `profile`, `cron_job`, `deliver` | `Route` in `team_kit_machine.py` | Written by this repository's code |
| `${VAR}` secret expansion from the root `.env` | `routes()` docstring and `hermes/README.md` | Recorded; **Pi** |
| GitHub HMAC check, refuses a bad `X-Hub-Signature-256` | `hermes/README.md`. The signed test in `setup-machine.sh` sends a valid signature only, so the refusal path is not tested here | Refusal path not exercised in this repository; **Pi** |
| `--cron-job` is validated against the default profile's jobs even with `--route-profile`, while the gateway fires the job in the routed profile at runtime | AGENTS.md learned facts | Not verified against 0.21.7. `team_kit_admin.py` writes routes straight into `config.yaml` with `config_set`, so the CLI validation does not apply to the installed path |
| Response code | `setup-machine.sh` requires `202` | Recorded |

Read-only commands for the bootstrap bot to run on the Hermes machine. None writes anything, and none prints a secret while the `grep -v` filter stays on.

```bash
hermes --version
command -v hermes && readlink -f "$(command -v hermes)"
hermes webhook --help
hermes webhook subscribe --help
hermes webhook list
hermes cron --help
for p in default $(team-kit-chief projects | awk 'NF{print $1}'); do echo "== $p"; hermes -p "$p" cron list --all; done
grep -v -i -E 'secret|token|key|password' ~/.hermes/config.yaml | sed -n '/platforms:/,/^[a-z]/p'
systemctl --user list-units --all 'cloudflared*'; systemctl list-units --all 'cloudflared*'
```

To check where `--cron-job` is validated without touching the real installation, read the installed package's code: `grep -rn "cron_job\|cron-job" "$(dirname "$(readlink -f "$(command -v hermes)")")/.."`. Record the version, the file and the line. Never run `subscribe` against the real `~/.hermes`, never bind a job to the default profile, and never message the default Hermes agent.

## Event pipeline design

Everything in this section is **desired**. None of it is implemented. Each requirement states the rule, then how the code will meet it, and names the unit that owns it.

| Requirement | Design |
|---|---|
| Signed receipt (#156) | A GitHub App (or per-repository webhook, decision 3) delivers to one receiver. It verifies `X-Hub-Signature-256` with HMAC-SHA256 over the **raw request bytes**, using a constant-time compare, before parsing JSON and before anything is stored. Anything else gets 401, and nothing from the body is recorded |
| Binding (#156) | After the signature, require: `repository.full_name` is a repository in `bots.yaml`; for an App, `installation.id` and the App id match the configured values (`X-GitHub-Hook-Installation-Target-ID`); `sender.login` is recorded and, for andon and proposals, only a login in `trusted_authors` counts, as `andon_issues` does today. A bound event only wakes a check. It never carries authorization |
| Durable delivery record (#156) | A SQLite database in WAL mode at `~/.hermes/team-kit/events/queue.db`, mode 600, standard library only. One row per delivery: `delivery_id` (unique), `repo`, `event`, `action`, `sender`, `head_sha`, `received_at`. The row holds a digest and these fields, not the payload body |
| Acknowledge after commit (#156) | The order is fixed: raw-body HMAC, then insert the unique delivery record, then COMMIT, then 202. A crash before the commit yields no 2xx, so GitHub redelivers |
| Dedupe without early coalescing (#156) | `X-GitHub-Delivery` is the unique key. A repeat insert is a success with no new work. Nothing is coalesced before the commit: every delivery gets its own committed row. A consumer may collapse many committed rows for one repository into one run |
| Per-consumer lease fencing (#156) | Each consumer claims rows under a lease with a fencing token. A consumer whose lease expired cannot commit a result, so a slow or restarted consumer never acts on a row another consumer has taken |
| Per-project lock and idempotent transitions (#157) | Specification, not implemented. The event worker and the temporary `tick` take one common per-project lock (one `flock` per bot, the pattern `check_app_review` already uses for one unit), so they never run one project at the same time. Different projects run in parallel. Every lifecycle transition (launch, gate, advance, merge, close) is idempotent: it re-reads GitHub, checks the current label state and the live head, and does nothing if the transition already happened. With both, the worker and the tick can run side by side during the cutover and converge on the same state. The lifecycle worker unit #157 owns this |
| Stale SHA (#157) | The worker never trusts `head_sha` from the event. It re-reads the pull request head from GitHub, and every existing gate (`--match-head-commit`, exact-head CI, review and verifier verdicts) compares against that live head. An event for an old head finds no work and ends |
| Retries (#157) | Up to 5 attempts at 30s, 2m, 10m, 1h, 6h with jitter, then dead letter. The next attempt time is stored, so a restart resumes the schedule. The worker takes `now` as a parameter, as the paved path requires |
| Dead letter (#157) | A dead row stays in the table, never deleted automatically. `team-kit-admin status` shows the count per project, and one notice is sent when the first row dies. R4 picks the work up regardless, so a dead row never strands a unit |
| Startup reconciliation (U6) | On listener start and on gateway start, run R4 once, then compare recent GitHub deliveries against the table through the REST deliveries endpoint, and request redelivery of any missing id |
| Outbound first | The public listener stays off. Until HTTPS and the domain are verified through the bootstrap workflow, the only active mechanism is today's outbound polling. GitHub has no outbound event stream, so a stage that needs no inbound path can only shadow polling, not replace it. The Tunnel config already publishes only exact signed paths and answers 404 otherwise, and that remains the rule |
| Shadow observation (U6) | Stage 1. The receiver records events and the check each would trigger, and does nothing else. The tick keeps making every transition. A report compares "event arrived" to "tick found the change" for at least one week per project. The pass bar is zero missed events for events that did produce a change |
| Staged cutover (U6) | Stage 2, per project, one at a time: the event worker makes transitions, the tick drops to the residual timers, and shadow comparison continues. Stage 3: widen to all projects. The chief and the owner approve each stage by a change to `bots.yaml`, not by an event or a model decision |
| Rollback (U6) | Stop the event worker and resume the paused cron jobs. Jobs are paused, never deleted, until a stage has run a week without a dead letter. Rollback needs no data migration because GitHub labels remain the unit state |

Gates that stay exactly as they are: exact-head CI, risk scoring, verification where configured, the andon cord, and `auto_merge: low`. Review follows decision 4.

## What each later unit owns

| Unit | Owns |
|---|---|
| U1, [#156](https://github.com/tsnAnh/faker-kit/issues/156) | Receiver: raw-body HMAC, binding, the durable unique delivery record, commit before 202, dedupe, per-consumer lease fencing, still off the public internet |
| U2 | Bootstrap inbox and the local wake events (T6, T1e) |
| U3, [#157](https://github.com/tsnAnh/faker-kit/issues/157) | Lifecycle worker: the common per-project lock and idempotent transitions shared with the temporary tick (T1 and its sub-triggers), stale-head behavior, retries and dead letter |
| U4 | `push` events for security (T3) |
| U5 | Default branch check events for incidents (T4), with health and alert polling kept (R3) |
| U6 | Reconciliation and deadline timers (R1, R2, R4 to R7), shadow report, cutover and rollback tooling |

## Decisions only the owner can make

1. **Residual timer cadences.** Hourly is proposed for R1 and R4. Shorter costs GitHub API calls and gives faster repair of a lost event. Longer saves calls and leaves a stuck unit longer. Say if a unit may sit longer than an hour past a 24 hour deadline.
2. **Sharing R1 and R4.** One hourly timer per project bot instead of two, allowed only once #157's implementation and tests prove the shared timer covers both.
3. **GitHub App or repository webhooks.** An App gives one delivery path, installation binding and a redelivery API, and needs a private key stored on the Hermes machine. Repository webhooks need no key beyond the per-bot secret. Neither is configured today.
4. **Review authority.** Settled: the review is the Claude `/code-review` command, run after implementation. Its output is a record, not a GitHub app approval. `review_app` in `.agents/dispatch.json` stays `claude[bot]`, and `check_app_review` in `team_kit_gh.py` is unchanged, so the merge gate still reads the review app's verdict on the exact head.
5. **Public listener go or no-go.** Which domain, and when HTTPS counts as verified.
6. **Event subscriptions.** Whether to add `pull_request_review_comment`, `check_run` or `workflow_run`, and default-branch checks. Each adds load and a larger public surface.
7. **Health targets and external alert sources.** Keep polling them (R3), and the cadence.
8. **Backups and maintenance of `~/.hermes` state.** None exist in this repository. Whether the Hermes machine has any, and whether the migration should add one.
9. **Weekly digest, upstream sync and inbox fallback.** Confirm R5, R6 and R7 stay timers.
10. **Dead-letter notice channel.** Bot Chat only, or also Slack.
11. **Shadow window.** One week per project is proposed.
12. **Tunnel persistence.** Whether `cloudflared` gets a supervised service before the public listener goes on.

## Verifying this page

The inventory is read from the code. Rows marked **Pi**, the chief's jobs and the live intervals need the commands above. The page itself changes no job, guard setting, workflow or secret store.
