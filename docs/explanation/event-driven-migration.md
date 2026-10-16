# Moving Hermes from polling to events

This page is the inventory and design for moving the Hermes installation from scheduled and polling jobs to event-driven operation. It changes no behavior. Later units implement it. Every row below traces to a file in this repository. Facts that only the Raspberry Pi can show are marked **Pi**, with the exact read-only commands in [Hermes capabilities](#hermes-capabilities).

## What already exists

The kit is not purely polled today. `webhooks` in the machine config (`hermes/lib/team_kit_machine.py`, `hermes/bin/team_kit_admin.py` `webhooks()`) already gives each project bot two signed Hermes webhook routes on `127.0.0.1:8644`, published through a Cloudflare Tunnel:

| Route | GitHub events | Fires |
|---|---|---|
| `<bot>-tick` | `issues`, `issue_comment`, `pull_request`, `pull_request_review`, `check_suite` | the `team-kit-tick` no-agent job |
| `<bot>-security` | `push` | the `team-kit-security` no-agent job |

`ROUTE_JOBS` in `team_kit_machine.py` is the list. The schedule stays as the fallback for a missed delivery. `setup-machine.sh` step 7 sends one signed test event per bot and requires a 202 before it finishes. So the migration extends a working path. It does not start one.

What the current path lacks, from the code:

- No durable queue. A route fires a cron job. Whether Hermes acknowledges before or after the job starts, and what it does with a delivery that arrives while the job runs, is a **Pi** fact.
- No delivery-id dedupe and no per-event record. Idempotence comes from `tick` re-reading GitHub each run.
- No dead-letter view. A failed run surfaces only as a cron failure notice.
- Event coverage gaps. `pull_request_review_comment`, `check_run`, `workflow_run` and commit `status` are not subscribed. The default branch's checks (incidents) have no route at all.
- `tick` takes no input from the event. It re-reads everything every time, so one event costs a full tick.

## Principle for the migration

An event is a **hint to run a deterministic check**, never an instruction and never authorization. The worker re-reads GitHub for current state, so a duplicate, a stale or a reordered event is harmless, and a missed event is repaired by reconciliation. This keeps the Hermes invariants in the root AGENTS.md: code decides what launches and gates, no model chooses, event text never becomes owner authorization.

## Inventory

Columns: id, owner profile, mechanism and interval, source, what it reads or does, event available, proposed trigger, residual timer, LLM use, migration unit. "Unit" names a unit this design proposes; the owner sequences them.

Default intervals are from `hermes/plugin/team_kit_guard/policy.py` (`DEFAULT_TICK_EVERY` 30m, `DEFAULT_INCIDENTS_EVERY` 10m, `DEFAULT_SECURITY_EVERY` 15m). The machine config template sets `tick_every: 10m` (`hermes/templates/machine.yaml`). The live values are **Pi** (`hermes -p <bot> cron list --all`).

### Hermes cron jobs, project bots

Source for all rows: `hermes/cron/project-jobs.md`, `hermes/bin/team_kit_admin.py` (`ensure_jobs`), `hermes/bin/team_kit_gh.py`. LLM use is none for every job (`--no-agent`).

| Id | Mechanism | What it does | Event available | Proposed trigger | Residual timer | Unit |
|---|---|---|---|---|---|---|
| T1 `team-kit-tick` | cron `every 30m` default, plus webhook route `<bot>-tick` | The umbrella run. Sub-triggers T1a to T1k below | Yes, for most parts | Queue worker per project runs `tick` on a coalesced event | Yes, see R1, R2, R4 | U3 |
| T2 `team-kit-status` | cron `0 9 * * 1` (Asia/Saigon) | Weekly digest of units in flight, waiting and done (`Project.status`, marker file `status/<bot>.json`) | No, a digest is time-defined | None | Yes, R5 | U6 |
| T3 `team-kit-security` | cron `every 15m` plus webhook route `<bot>-security` on `push` | Starts one cloud security review when the default branch moved and none is running (`Security`) | Yes, `push` to the default branch | Push event, filtered to the default ref, enqueues the job | No for the start; the 24h no-result deadline is R1 | U4 |
| T4 `team-kit-incidents` | cron `every 10m`, no route | Default branch checks, health URLs, external alert sources, triage session, auto close (`Incidents`) | Default branch checks: yes (`check_suite`, `workflow_run`). Health URLs: no. External alerts: not from GitHub | Subscribe `check_suite`/`workflow_run` for the default branch. Health and alert sources stay polled | Yes, R3 | U5 |
| T5 `team-kit-upstream` | cron `0 9 * * 1` | Compares the upstream repo head with `upstream/source.json`; dispatches one sync unit (`Upstream`) | No. The upstream repository is not ours, so we cannot add a webhook | None | Yes, R6 | U6 |

`team-kit-gh tick` internals, each its own trigger because each has its own event or none. File: `hermes/bin/team_kit_gh.py`, `Project.tick` and the methods named.

| Id | Detects | Event available | Deterministic decision | Residual timer |
|---|---|---|---|---|
| T1a andon sync (`sync_andon`) | Open `agent:andon` issue by a trusted login; cord cleared | `issues` (opened, labeled, closed, unlabeled) | Raise or clear the local cord from the trusted-author list | No |
| T1b teammate proposals (`proposal_notices`) | New `agent:proposal` issue | `issues` labeled | Collaborator check, one notice per proposal | No |
| T1c PR comment mentions (`pr_comment_notices`) | A collaborator's PR comment that mentions the bot | `issue_comment` (subscribed). `pull_request_review_comment` is not | Notice with link, never the text. The record `since` time stays as the reconciliation anchor | No, once the event is added |
| T1d kit PRs (`kit_prs`) | Kit vendoring pull requests that match the local kit commit | `pull_request`, `push` | Compare PR head to the local commit, notify once | No |
| T1e unit-state publish (`publish`, `UnitStates`, `~/.hermes/team-kit/units/<bot>.json`) | Closed or changed units that another project bot's briefs depend on | The closing event happens in the other bot's repository, so this bot's webhook never sees it | Needs a local event: when bot A publishes, wake the bots whose `wants` list names A | Yes, folded into R4 as the fallback |
| T1f running units (`check_running`) | A signed done report comment; a stopped cloud task | Report comment: `issue_comment`. Cloud task end (`codex cloud status`): no event | Parse the signed report, gate or advance | Yes, R1 (24h no-report deadline) and R2 (cloud task status) |
| T1g verifying units (`check_verifying`) | Verifier verdict comment, PR head change | `issue_comment`, `pull_request` (synchronize) | Exact-head verdict, risk score, optional auto merge | Yes, R1 (verifier deadline) |
| T1h review and auto-merge retries (`check_app_review`, `auto_merge`) | Review app verdict on the exact head; a merge GitHub refused earlier; cord cleared | `pull_request_review`, `check_suite`, andon `issues` event | Merge only when risk is low, checks passed, head matches (`--match-head-commit`) | No for the verdict. A retry after a transient refusal needs R4 |
| T1i pending checks (`check_running` tail) | Checks passed or failed on the reported head; 1 hour no-checks grace; 24 hour pending deadline | `check_suite` completed | Gate on failure, advance on pass | Yes, R1 (`NO_CHECKS_GRACE_SECONDS`, `REPORT_DEADLINE_SECONDS`) |
| T1j launch (`launch`) | A queued unit whose dependencies merged and a free slot | `issues` closed (dependency), label changes (slot freed), local `units/` change for cross-project | Dependencies, `max_in_flight`, andon, kit pin checks, all in code | No for same-project. Cross-project is T1e |
| T1k security check inside tick (`Security.check`) | Signed security result comment; stopped task; 24h no-result | `issue_comment` on the `agent:security` issue | Verify the HMAC signature, notify only on high or medium | Yes, R1, R2 |
| T1l in-flight counts (`InFlight`, `IN_FLIGHT_FRESH_SECONDS`, `UNIT_STATE_FRESH_SECONDS`, both 24h) | Staleness of the shared counts and unit states other bots read | No | A stale record stops counting after 24 hours | Hidden dependency: with no tick, counts silently expire. Folded into R4 |

### Hermes cron jobs, bootstrap bot

| Id | Mechanism | What it does | Event available | Proposed trigger | Residual timer | Unit |
|---|---|---|---|---|---|---|
| T6 `team-kit-inbox` | cron `every 30m`, no agent. Source `hermes/cron/bootstrap-jobs.md`, `team_kit_admin.py` `inbox` | Delivers one waiting chief project request as a notice | Yes, a local one. `team-kit-chief new-project` writes a request file (`lib/team_kit_requests.py`) and already wakes the job with `hermes cron run` (`wake_job` in `team_kit_chief.py`) | Keep the wake; make it the primary path with the same worker queue | The 30 minute run becomes the fallback inside R4 | U2 |

### Chief profile

`hermes/profiles/chief/config.yaml` defines no schedule, and `ROLE_JOBS` in `team_kit_admin.py` creates jobs only for the `project` and `bootstrap` roles. The chief has no cron job in this repository. **Pi:** confirm with `hermes -p chief cron list --all` that nobody added one by hand, and list the default profile's jobs too. The brief asked for chief schedules, so the Pi result is required before this row is final.

### Machine services

Source: `hermes/setup-machine.sh`, `hermes/lib/team_kit_machine.py`, `hermes/bin/team_kit_runner.py`, `hermes/lib/team_kit_slack_relay.py`.

| Id | Mechanism | What it does | Event source | Timer content | Proposed | Unit |
|---|---|---|---|---|---|---|
| T7 Hermes gateway | launchd/systemd service from `hermes gateway install` | Serves every profile, the webhook listener and cron | n/a | Supervisor restart only | Unchanged | none |
| T8 Cloudflare Tunnel | `service_user` unit, `Restart=always RestartSec=10` | Publishes only `/p/<bot>/webhooks/<route>` | n/a | Supervisor restart | Unchanged. The public listener stays off until HTTPS and the domain are verified | U1 |
| T9 CI runners `team-kit-ci-<repo>-<slot>` | `Restart=always RestartSec=10`, loop in `Slot.forever` with `BACKOFF_SECONDS = (30, 60, 120, 300)` | Registers a just-in-time runner, waits for one job, runs a container, removes it | GitHub assigns the job to the registered runner, so it is event-driven already. The loop is a supervisor with backoff | Backoff after failure only | Unchanged | none |
| T10 Slack relay | `team-kit-slack run`, Socket Mode over an outbound WebSocket, reconnect backoff up to 300s (`SlackRelay.run`) | Relays app mentions into Bot Chat; mirrors notices out | Slack Socket Mode events, outbound only | Reconnect backoff | Unchanged. Its event ids are the dedupe model reused below (`event_keys`, `seen`) | none |
| T11 Notice delivery | Hermes `bot-chat:<bot>` delivery of each job's output; `mirror_notice` | Shows each notice | The job output itself | None | Unchanged | none |
| T12 `setup-machine.sh`, `install.sh` | Run by hand | `sleep` loops wait for the listener and tunnel during setup only | n/a | Setup waits, not runtime triggers | Unchanged | none |

Repository workflows: `.github/workflows/` defines no `schedule:` trigger (checked with a search). `docs-site.yml` and the review workflow run on push and pull request events already.

Not found in this repository: backup jobs, log rotation, update jobs, or any maintenance timer. **Pi:** the commands below list anything the owner added by hand.

## Residual timers

These triggers have no external event or need a deadline. They stay timers. The honest count is **six scheduled timers**, plus the process supervisors in T7 to T10 that restart a dead service and are not job triggers. The migration does not reach zero timers.

| Id | Timer | Minimal design | What is lost if removed |
|---|---|---|---|
| R1 | Deadline sweep for silence: no agent report in 24h, checks pending 24h, verifier, review and security and triage with no signed result in 24h, the 1 hour no-checks grace | One timer per project, hourly. It runs only the deadline checks of `tick` (read-only except gating). Alternative: schedule a one-shot per unit at launch; the hourly sweep is simpler and survives restarts | A session that dies silently would hold its slot forever. Silence produces no GitHub event |
| R2 | Cloud task status (`codex cloud status`) | Poll only while at least one Codex task is running, every 5 minutes, no timer when none runs | A finished task with no comment would wait for the 24h deadline instead of being reported in minutes |
| R3 | Health URLs and external alert sources (Datadog, Sentry, Grafana, PagerDuty in `lib/team_kit_incident_sources.py`) | Outbound poll every 5 to 10 minutes, only when `.agents/dispatch.json` configures a target. Pull-only keeps the outbound-first rule | A target that goes down with no deploy would never open an incident |
| R4 | Reconciliation: GitHub outage, a missed or dropped delivery, listener downtime, the cross-project unit-state change (T1e), merge retries (T1h), the 24h staleness of T1l, and the bootstrap inbox fallback (T6) | One idempotent full `tick` per project every 1 hour, plus one full `tick` on listener start and on gateway restart. A GitHub deliveries cross-check (`GET /repos/{owner}/{repo}/hooks/{id}/deliveries`, then redelivery) runs inside it | Any single lost event would leave a unit stuck until a human noticed |
| R5 | Weekly status digest (T2) | Keep `0 9 * * 1` | The digest. No event describes "a week passed" |
| R6 | Weekly upstream sync check (T5) | Keep `0 9 * * 1`. If the owner prefers, an upstream release webhook is impossible without owning that repository | New upstream versions would go unnoticed |

R4's cadence is the main reliability and cost tradeoff. See decisions 1 and 2.

## Hermes capabilities

**This session could not read the public Hermes documentation.** Outbound HTTPS to `hermes-agent.nousresearch.com` returned `CONNECT tunnel failed, response 403` from the cloud session's proxy, and Hermes is not installed here (`which hermes` prints nothing). I therefore did not verify the claims below against the upstream source. They are what this repository records, with the file that records each. Nothing here was invented.

| Capability | Recorded in the repository | Status |
|---|---|---|
| Webhook platform on a loopback listener, `platforms.webhook` with `enabled`, `extra.host`, `extra.port`, `extra.routes` | `team_kit_admin.py` `webhooks()` writes these keys; `hermes/README.md` lists them | Recorded, written by this repo; **Pi** to confirm version support |
| Route fields `events`, `secret`, `profile`, `cron_job`, `deliver` | `Route` in `team_kit_machine.py` | Recorded; **Pi** |
| `${VAR}` secret expansion from the root `.env` | `routes()` docstring and `hermes/README.md` | Recorded; **Pi** |
| GitHub HMAC check, refuses a bad `X-Hub-Signature-256` | `hermes/README.md`; the signed test in `setup-machine.sh` sends a valid signature only, so the refusal path is not tested here | Recorded, refusal path not exercised in this repo; **Pi** |
| `hermes webhook subscribe`, `--cron-job`, `--route-profile` | AGENTS.md learned facts | Recorded; **Pi** |
| `--cron-job` is validated against the default profile's jobs even with `--route-profile`, while the gateway fires the job in the routed profile at runtime | AGENTS.md learned facts | **Neither confirmed nor refuted here.** Supporting evidence: this repo never uses the CLI. `team_kit_admin.py` writes the route straight into `config.yaml` with `config_set`, so the CLI validation does not apply to the installed path, and `setup-machine.sh` step 7 proves a signed event fires the routed profile's tick on the Pi when it passes |
| Response code | `setup-machine.sh` requires `202` | Recorded |

Exact read-only commands for the bootstrap bot to run on the Pi. None writes anything, and none prints a secret as long as the `grep -v` filters stay on.

```bash
hermes --version
command -v hermes && readlink -f "$(command -v hermes)"
hermes webhook --help
hermes webhook subscribe --help
hermes webhook list
hermes cron --help
for p in default $(team-kit-chief projects | awk 'NF{print $1}'); do echo "== $p"; hermes -p "$p" cron list --all; done
grep -v -i -E 'secret|token|key|password' ~/.hermes/config.yaml | sed -n '/platforms:/,/^[a-z]/p'
```

To confirm or refute the profile-validation limitation without touching the real installation, run in a throwaway home (this writes only to the temp directory):

```bash
export HERMES_HOME="$(mktemp -d)"
hermes -p default webhook subscribe --help   # read the flags the installed version accepts
```

Then read the installed package's webhook subscribe code for where `--cron-job` is checked: `grep -rn "cron_job\|cron-job" "$(dirname "$(readlink -f "$(command -v hermes)")")/.."`. Compare it with the upstream source for the same version, record the version and the file and line, and do not run `subscribe` against the real `~/.hermes`. Never bind a job to the default profile and never message the default Hermes agent.

## Event pipeline design

The later units implement this. Each requirement states the rule, then how the code meets it.

| Requirement | Design |
|---|---|
| Signed receipt | A GitHub App (or per-repository webhook, decision 3) delivers to one receiver. Verify `X-Hub-Signature-256` with HMAC-SHA256 over the **raw request bytes**, using a constant-time compare, before parsing JSON and before anything is enqueued. Reject anything else with 401 and record nothing from the body |
| Binding | After the signature, require: `repository.full_name` is a repository in `bots.yaml`; for an App, `installation.id` and the App id match the configured values (`X-GitHub-Hook-Installation-Target-ID`); `sender.login` is recorded and, for andon and proposals, only a login in `trusted_authors` counts, as `andon_issues` does today. A bound event only wakes a check. It never carries authorization |
| Durable queue | A SQLite database in WAL mode at `~/.hermes/team-kit/events/queue.db`, mode 600, standard library only. Row: `delivery_id` (primary key), `repo`, `event`, `action`, `sender`, `head_sha`, `received_at`, `state`, `attempts`, `next_at`, `last_error`. The row holds a digest and the fields above, not the payload body |
| Acknowledge after durable receipt | Return 2xx only after the row's commit succeeds. A crash before commit yields no 2xx, so GitHub redelivers |
| Dedupe | `X-GitHub-Delivery` is the primary key. A repeat insert is a success with no new work. Also coalesce: many pending rows for one repository collapse into one worker run |
| Per-project lock | One `flock` per bot, the pattern `app_review_locked` already uses, so two workers never run one project. Different projects run in parallel |
| Stale SHA | The worker never trusts `head_sha` from the event. It re-reads the pull request head from GitHub and every existing gate (`--match-head-commit`, exact-head CI, review and verifier verdicts) compares against that live head. An event for an old head finds no work and ends |
| Retries | Up to 5 attempts at 30s, 2m, 10m, 1h, 6h with jitter, then dead letter. `next_at` is stored, so a restart resumes the schedule. The worker takes `now` as a parameter, as the paved path requires |
| Dead letter | State `dead` stays in the table, never deleted automatically. `team-kit-admin status` shows the count per project, and one notice is sent when the first row dies. R4 picks the work up regardless, so a dead row never strands a unit |
| Startup reconciliation | On listener start and on gateway start, run one full `tick`, then compare recent GitHub deliveries against the table through the REST deliveries endpoint, and request redelivery of any missing id |
| Outbound first | The public listener stays off. Until HTTPS and the domain are verified through the bootstrap workflow, the only active mechanism is today's outbound polling plus R4. GitHub has no outbound event stream, so a stage that needs no inbound path can only shadow, not replace, polling. The Tunnel config already publishes only exact signed paths and answers 404 otherwise, and that remains the rule |
| Shadow observation | Stage 1. The receiver records events and the check each would trigger, and does nothing else. Polling is the sole authority. A report compares "event arrived" to "tick found the change" for at least one week per project. The pass bar is zero missed events for events that did produce a change |
| Staged cutover | Stage 2, per project, one at a time: events become authority, polling drops to R4 (hourly), shadow comparison continues. Stage 3: widen to all projects. The chief and the owner approve each stage by a change to `bots.yaml`, not by an event or a model decision |
| Sole authority | A single field per project, `authority: poll` or `authority: event`, in a state file under `~/.hermes/team-kit/`. Only the holder may launch, gate or merge. The other mode runs read-only and logs. The per-project lock makes a double run safe, and the field makes the owner of the decision unambiguous |
| Rollback | Set `authority: poll` and resume the paused cron jobs. Jobs are paused, never deleted, until a stage has run a week without a dead letter. Rollback needs no data migration because GitHub labels remain the unit state |

Gates that stay exactly as they are: exact-head CI, risk scoring, verification where configured, the andon cord, and `auto_merge: low`. Review follows decision 4.

## What each later unit owns

| Unit | Owns |
|---|---|
| U1 | Listener, signature check, binding, queue, dedupe, dead letter, still off the public internet |
| U2 | Bootstrap inbox and the local wake events (T6, T1e) |
| U3 | Worker and per-project lock for `tick` (T1 and its sub-triggers), coalescing, stale-head behavior |
| U4 | `push` events for security (T3) |
| U5 | Default branch check events for incidents (T4), with health and alert polling kept (R3) |
| U6 | Reconciliation and deadline timers (R1, R2, R4, R5, R6), shadow report, cutover and rollback tooling |

## Decisions only the owner can make

1. **Reconciliation cadence (R4).** Hourly is proposed. Shorter costs GitHub API calls and gives faster repair of a lost event. Longer saves calls and leaves a stuck unit longer.
2. **Deadline sweep cadence (R1).** Hourly is proposed against 24 hour deadlines. Say if a unit may sit longer.
3. **GitHub App or repository webhooks.** An App gives one delivery path, installation binding and a redelivery API, and needs a private key stored on the Pi. Repository webhooks are what exist today and need no key beyond the per-bot secret.
4. **Review authority.** The brief says Codex managed automatic review is chosen and Claude review must not be set up. `.agents/dispatch.json` still sets `review_app` to `claude[bot]`, and `check_app_review` reads that app. Which app's verdict the event pipeline waits for must be settled before U3. This unit changed neither.
5. **Public listener go or no-go.** Which domain and when HTTPS counts as verified.
6. **Event subscriptions.** Add `pull_request_review_comment`, `check_run` or `workflow_run`, and default-branch checks? Each adds load and a larger public surface.
7. **Health targets and external alert sources.** Keep polling them (R3), and the cadence.
8. **Backups and maintenance of `~/.hermes` state.** None exist in this repository. Say whether the Pi has any, and whether the migration should add one.
9. **Weekly digest and upstream sync.** Confirm both stay timers (R5, R6).
10. **Dead-letter notice channel.** Bot Chat only, or also Slack.
11. **Shadow window.** One week per project is proposed. Say if a shorter or longer window fits.

## Verifying this page

The inventory is read from the code, not run on a live installation. Rows marked **Pi**, the installed Hermes version, the chief's jobs and the live intervals need the commands above. Nothing in the repository was changed to produce this page: no job, guard setting, workflow or secret store.
