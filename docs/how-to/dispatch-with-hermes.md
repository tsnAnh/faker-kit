# Dispatch work with Hermes

You tell Hermes what you want in Hermes Desktop. It splits the work into units, opens one GitHub issue per unit, and starts a Claude Code or Codex cloud agent on each. Every unit ends as a pull request with passing checks. You answer the questions agents cannot, and you merge. Nothing else needs you.

## How it fits together

| Part | Runs where | Does |
|---|---|---|
| Hermes Desktop | Your computer | The only place you talk to Hermes |
| `dispatcher` profile | The Hermes machine, strong model | Splits your intent into Kanban cards, writes a brief per card, opens the issue with `team-kit-gh`, moves cards when GitHub events arrive. Loads the [dispatch](../../plugins/trust-ladder/skills/dispatch/SKILL.md) skill |
| `launcher` profile | The Hermes machine, cheap model | The Kanban assignee of every card. Runs the one launch command on the card and blocks it "waiting for PR" |
| Coding agent | Claude Code cloud (`claude --cloud`) or Codex cloud (`codex cloud exec`) | Works the brief in faker-mode with the [follow-brief](../../plugins/trust-ladder/skills/follow-brief/SKILL.md) skill, opens one pull request, posts a report |
| Gateway | The Hermes machine, in the background | Receives GitHub webhooks, runs the cron jobs and the Kanban dispatcher |

One unit goes like this:

1. You state the work in Desktop. The dispatcher restates the units and asks you to confirm.
2. Per unit, it creates a blocked card, writes the brief, and runs `team-kit-gh open-issue`. The issue is labeled `agent-task`.
3. It comments the launch command on the card and unblocks it. A card with unfinished dependencies waits.
4. The launcher runs the command. The coding agent checks the issue came from a trusted login with the brief marker, does the work, opens a pull request with the PR marker, and comments a report.
5. GitHub sends `pull_request`, `issue_comment`, and `check_suite` events to the gateway. `github-triage.py` drops everything that does not change a unit and wakes the `dispatcher-drain` job for the rest.
6. When the pull request's required checks pass, the drain completes the card. You review and merge.

Each repository picks its coding agent once, in `.agents/dispatch.json` (`backend` is `claude` or `codex`).

The launch command `team-kit-gh open-issue` prints, and the launcher runs, is one of these. `N` is 1, 2, or 3 for a small, medium, or large unit.

```bash
claude --cloud '/faker-mode Work the brief in OWNER/REPO#123 using the follow-brief skill'
codex cloud exec --env ENV_ID --attempts N '$faker-mode Work the brief in OWNER/REPO#123 using the follow-brief skill'
```

The prompt always starts with `/faker-mode` or `$faker-mode`. The `faker-mode` skill cannot load itself, so the prompt has to run it as a command.

## Install both sides

Run [setup-dispatch](../../plugins/trust-ladder/skills/setup-dispatch/SKILL.md). It walks through both sides and proves the loop with one small unit. The short version:

- **The repository.** `.agents/dispatch.json` and `.agents/dispatch.md` from [`templates/dispatch/`](../../plugins/trust-ladder/templates/dispatch/), committed through a pull request. Branch protection with required checks. The labels `agent-task` and `agent:needs-human`.
- **The Hermes machine.** From a clone of the kit, `bash plugins/trust-ladder/hermes/install.sh --ref <commit>`. Then fill `repos` and `trusted_authors` in `~/.hermes/team-kit/guard.yaml`, give each profile its own `GH_TOKEN`, and point a GitHub webhook at the gateway. New scheduled jobs are installed paused; resume them only after these prerequisites and the repository's cloud-agent setup are verified.

The installer and skill linker work with macOS's bundled Bash 3. They need a Python 3.9+ executable that can import PyYAML. The installer checks the Python shebang on Hermes, then `python3` on `PATH`; managed Hermes shell launchers are not treated as Python executables. If neither candidate can import PyYAML, install it in a helper Python and pass that executable as `HERMES_PYTHON`. An explicit override is validated and never silently replaced. Hermes configuration is applied through `hermes config set`, with readback checks rather than direct YAML rewrites.

An existing route named `team-kit-github` must belong to this same dispatcher setup; a conflicting static or dynamic route stops installation instead of being overwritten. A standalone dispatcher must be explicitly migrated to the shared gateway first. Back up the profiles before migration and follow the current Hermes gateway documentation; the installer does not silently change gateway topology.

### Pin the same commit on both sides

`kit_ref` in `.agents/dispatch.json` and `--ref` for `install.sh` must name the same kit commit. The briefs, the markers, and the report format are a contract between the two sides, and both must read the same version of it. `team-kit-gh` refuses to open an issue while the commits differ. To take a newer kit, rerun `install.sh` with the new commit and change `kit_ref` in a pull request.

### Desktop and the background gateway

You chat in Hermes Desktop with the `dispatcher` profile. Current Hermes runs one background gateway from the default profile and serves both `dispatcher` and `launcher` through it. The installer registers the `team-kit-github` route on that shared listener, explicitly bound to `dispatcher`; a route only in the dispatcher's config is not enough. The kit does not connect a chat platform or change existing messaging channels. Questions and gates reach you as messages in the dispatcher's Desktop Bot Chat.

### Use an always-on machine

Cron, the webhook, and the Kanban dispatcher run only while the gateway runs. Put Hermes on a home server or a small VM, not a laptop that sleeps. Desktop can connect to it from anywhere.

### The webhook tunnel

GitHub must reach the shared webhook listener over HTTPS (port 8644 on a fresh install). Use a tunnel or a reverse proxy with TLS, such as Cloudflare Tunnel or Tailscale Funnel, and expose only that listener. The webhook URL is `https://<public host>/p/dispatcher/webhooks/team-kit-github`, signed with the secret in `~/.hermes/team-kit/webhook-secret`. Do not omit the profile prefix: the route's signature is bound to the dispatcher execution target.

Check `http://localhost:8644/health` for listener health, not `/p/dispatcher/health`. A signed GitHub `ping` to the route should return 200 with `status: ignored`; a request without a signature must return 401. If the host already has a webhook listener, use its configured port rather than changing it for team-kit.

### Activate the scheduled jobs

After the repository, credentials, backend login, and public webhook are ready, find the two job IDs with `hermes -p dispatcher cron list --all`, then resume them:

```bash
hermes -p dispatcher cron resume <dispatcher-drain-job-id>
hermes -p dispatcher cron resume <dispatch-status-job-id>
```

Reinstalling does not resume paused jobs or pause jobs you already activated. Keep them paused if you are only installing the machine-side components, rather than completing dispatch onboarding.

## Safety layers

None of these uses a model to decide. Each is code or configuration.

1. **Desktop is the only way in.** No chat platform is connected. Webhooks wake only the drain job.
2. **Least privilege per profile.** The dispatcher has Kanban, a terminal, files, `clarify`, and skills. The launcher has Kanban and a terminal. The webhook route runs no agent. It only fires the drain job, which runs as the dispatcher under the guard.
3. **The guard blocks.** The `team_kit_guard` plugin checks every tool call against `guard.yaml`. The dispatcher may run only `team-kit-gh` and write only briefs. The launcher may run only the exact launch commands. Merges, pushes to `main`, force-pushes, and deletes are denied for both. Any error, a timeout, or a missing `guard.yaml` blocks the call.
4. **Untrusted text is marked.** Output from `team-kit-gh read` and web tools arrives wrapped in `<untrusted source=...>` and taints the turn. After that, every tool that changes something asks you to approve it in Desktop. Cron runs deny approvals, so a drain cannot act on text it should not trust.
5. **Briefs come only from you and the repository.** The dispatcher writes briefs from your message and the repository's files. The coding agent works only issues written by a login in `trusted_dispatchers` that start with the brief marker. The webhook counts only pull requests and reports by a login in `trusted_authors` in `guard.yaml`, so a copied marker cannot move a card.
6. **Checks and a human merge.** A card completes only when the repository's required checks pass on the pull request's head. Only you merge.

## What you do

- **State work** in Desktop, and confirm the units the dispatcher restates.
- **Answer gates.** When an agent reports `partial` or `blocked`, the issue gets `agent:needs-human` and you get one line in Bot Chat. Read the report, then answer on the issue or tell the dispatcher what to do next.
- **Merge** pull requests whose cards completed.

The `dispatch-status` job sends three lines at 9:00, 13:00, and 17:00 in the dispatcher profile's timezone (Asia/Saigon by default): what is in flight, what waits on you, and what is done since the last digest.

## Troubleshooting

- **Every tool call is blocked.** `~/.hermes/team-kit/guard.yaml` is missing, does not parse, or lacks `version: 1`. The guard fails closed on purpose. Fix the file. The block message names the problem.
- **"kit commit ... differs from kit_ref ...".** The Hermes install and `.agents/dispatch.json` pin different kit commits. Rerun `install.sh --ref` or change `kit_ref` so they match, then run `team-kit-gh kit-check --repo OWNER/REPO`.
- **"... already in flight (max_in_flight)".** The repository has that many open `agent-task` issues. The card stays queued, and the drain opens it after a unit merges. Raise `max_in_flight` in a pull request if the repository can take more.
- **A card is stuck "waiting for PR".** No pull request with `<!-- team-kit:pr v1 card=<card> issue=<n> -->` in its body exists yet. Check the session or task the launcher attached to the card. A Codex task that could not push leaves the pull request to you. Open it from the task and keep the marker in the body. If the pull request exists, check the webhook's Recent Deliveries in GitHub for errors.
- **A card does not complete though checks are green.** The repository has no required checks, or the launcher's `GH_TOKEN` cannot read them. Kanban reads checks as the launcher.
- **Desktop keeps asking you to approve tool calls.** That turn read untrusted text. Approve only a call you asked for. Otherwise deny it and ask again in a new turn, without the untrusted read.
- **Approvals never appear.** `approvals.mode` is `off`, or Hermes runs with `--yolo`. Set it to `manual` or `smart`. With `off`, Hermes approves the guard's requests by itself.
- **The health endpoint works but the route returns 404.** Check that `team-kit-github` is registered on the default profile's shared listener with `profile: dispatcher`, and use `/p/dispatcher/webhooks/team-kit-github`. A profile-local route alone is not registered on the shared listener.
- **No scheduled work runs after installation.** New jobs are intentionally paused. Finish onboarding, then resume both job IDs as above. Check `hermes -p dispatcher cron status` and manage the shared service with `hermes gateway status` from the default profile.

## Where this differs from the design

- **Launch commands use single quotes.** Inside double quotes the shell would expand `$faker-mode`.
- **The webhook route uses `cron_job` without `coalesce`.** Hermes does not allow both. The drain re-reads every open unit each run, so bursts of events are harmless.
- **Gates and digests go to Bot Chat.** Hermes cron delivers to `bot-chat:<profile>`, the profile's Desktop Bot Chat, and cannot target another named session. Hermes has no desktop notification of its own. Bot Chat delivery arrives as a turn the dispatcher answers, so every delivered line starts with `team-kit notice:`, and the guard taints any turn that contains it.
- **The webhook route sets no toolsets.** A route with `cron_job` runs no agent, so Hermes ignores its toolsets. The drain runs with the dispatcher profile's toolsets, under the guard.
- **Events during a drain are dropped, not queued.** Hermes refuses to start a cron job that is already running. The drain sweeps every open unit, including merged pull requests through `team-kit-gh pr-state`, and its two-hour schedule catches what a dropped event missed.
- **`codex_env` was added** to `.agents/dispatch.json`. `codex cloud exec` needs the Codex environment id.
- **Cards use Kanban's default retry limit.** The `kanban_create` tool takes no per-card retry count. To change the limit for every card, set `kanban.failure_limit` in the dispatcher profile's config, which the gateway reads.
