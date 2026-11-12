# faker-kit for Hermes

This module runs faker-kit dispatch on a [Hermes Agent](https://github.com/NousResearch/hermes-agent) machine: one bootstrap bot that sets projects up, one project bot per project that runs the project as its long-lived coordinator chat and starts Claude Code cloud sessions, one pull request per unit, and an optional chief of staff, an engineering lead that routes the owner's intent across projects and owns each part until it merges, on weekday jobs from `cron/chief-jobs.md`. How to use it is in [Dispatch work with Hermes](../docs/how-to/dispatch-with-hermes.md).

It lives outside `plugins/` on purpose. Nothing here loads in a coding agent: plugins are what coding agents load, and this is what the Hermes bots load.

## How it works

The project bot is a model. It follows faker-mode's Orchestrate, Autopilot, Babysit, and Shipping playbooks (the `dispatch` skill points at them): it writes a brief, starts a cloud session with `faker-kit launch --brief-file`, finds the session's work by its pull request, has a separate agent verify it, and merges. A unit has no GitHub issue and no label; the pull request is the only GitHub artifact. The bot's plan lives in its orchestrate store under `~/.hermes/faker-kit/briefs/<bot>/orchestrate/<project>/`.

Code keeps only what the model cannot talk past:

- The guard's hard blocks: secret reads, repository deletion, push to main or force-push, raw `gh pr merge` and the merge API, wiping `/` or the home folder.
- Scheduled jobs: bots manage their own with `cronjob_manage`. The guard scans a job's `script` and `command` like code and does not scan its `prompt`, which is prose for a later turn the guard checks call by call.
- `faker-kit merge --pr N` as the only way to land a pull request. It merges only when every check on the head passed and at least one passing check run on it belongs to a `push` or `pull_request` workflow run of this head in a workflow other than `.github/workflows/claude-review.yml` and `.github/workflows/faker-kit-checks.yml` (found through the head's workflow runs and each check run's check suite, so a check's name proves nothing; a `pull_request` run must come from this pull request's branch in this repository, start after the pull request opened, and be attached to this pull request and its base branch, a `push` run counts only when the head's `.github/workflows` folder equals the default branch's, and a run of any other event never counts), no person has a standing changes-requested review, the pull request is open, from this repository, against the default branch, and has no conflict (a `mergeable` GitHub has not computed yet is read again), no other open pull request has the same head, no branch of this pull request ever targeted another base and its base never changed, no andon cord is pulled, and the pull request touches none of `.github/workflows/`, `.agents/dispatch.json`, the guard files, or a path in `risk_paths_high` or `forbidden_paths` of `.agents/dispatch.json` on the default branch (those the owner merges). The merge is pinned to the head it checked, under a lock per bot. With `--owner-approved` (the owner's own `merge #N` message) the check and CI gates do not hold: pending, failed, or missing checks and CI, and a repository with no CI workflow, are bypassed and listed in the `bypassed` field of the output; `--wait <seconds>` (a whole number from 0 to 1800, default 0) first keeps the call polling every 30 seconds, reading evidence fresh each time, while the head's checks are only pending (the bypass applies after the wait, so with `--owner-approved` the bound passing still refuses): a failed check ends the wait at once and follows the normal rules, a head that moved during it refuses (nothing merges), the bound passing refuses and says how long it waited, and a merge prints `wait` (`seconds`, `polls`); it keeps no state; `--open` (a project bot's own project, or the chief's with `--project`), `--all-projects` (chief only) and `--match <text>` (open non-draft pull requests whose title or branch contains the text) pick the targets from the owner's request instead of a typed number, only with `--owner-approved`, never together with `--pr`: `--open` and `--all-projects` print one JSON line `{"resolved": [...], "skipped_drafts": [...], "skipped_not_owner": [...]}` first (they take only non-draft pull requests opened by the GitHub login `gh` is signed in as, read once per call and compared case-insensitively; another author's pull request is listed under `skipped_not_owner` with `project`, `number`, `title` and `author` and never merged, the login unreadable refuses and merges nothing, and no eligible pull request at all exits non-zero; `--match` counts only that login's candidates and, with none, names the other authors' matches in its refusal; `--pr N` merges whoever the author is because the owner named it, and adds `author` and `named_by_owner` to its output when that is not the machine login), then merge in ascending number order, project by project (projects in name order), one at a time through the same gate, with `--wait` defaulting to 600 seconds shared by all of them (a pull request the budget does not reach is `not attempted: wait budget used`) and a final JSON of per-pull-request results (merged with its `bypassed` list, or refused with the reason; exit 1 unless all merged); `--match` merges a single candidate through the normal path, refuses none, and for several prints `{"status": "ambiguous", "candidates": [...]}` and exits 3 with nothing merged; the andon cord, a conflict, a person's requested changes, and a pull request that is not open, from this repository, and against the default branch still refuse. Runs, jobs, check runs, and statuses of `.github/workflows/claude-review.yml` (the `review` and `security` jobs and the `security-review` status) are ignored: they neither pass nor block a merge, and no review, `Risk:` line, or verdict is read.
- `faker-kit launch` refuses while an andon cord is pulled.

The andon cord is an instruction in the skills. A session may run the project's own verification skills on its own change. The cord is a local record plus an open `agent:andon` issue per repository; the briefs tell cloud agents to check for it before every push.

Each project bot has one agent job, `faker-kit-babysit`, running the `dispatch` skill and delivering to the bot's chat (woken by pull request, review, check, and status events and by the `faker:fix` label on an issue, with an hourly tick as the fallback; a gate script runs `faker-kit issues` and `faker-kit status` and never wakes the agent while no pull request is open, no issue is listed, and no `faker-kit notice:` line printed, with no first-run wake and no heartbeat; otherwise it lets a tick through only when an issue is listed, a notice printed, the last open pull request merged or closed (once, then quiet again), a pull request line changed (each line carries the pull request's `updated` time, so a new comment or review counts), six hours passed since it last woke the agent while a pull request is open, or the job's previous run after that wake did not finish ok, which wakes it at most three times in a row and never when the run list cannot be read; it launches one session from `templates/fix-issue-brief.md` per listed issue). No job polls for incidents: the owner asks a project bot, which runs `faker-kit incidents`, or connects the chief to an incident service. `install` and `add-project` remove the retired `faker-kit-issues`, `faker-kit-security`, and `faker-kit-incidents` jobs and their gate scripts. The chief's `faker-kit-loop` job has a gate too: it runs `faker-kit loop --all` and wakes the chief only when a waiting part is due or `to_report` lists a part.

## The command

One command, `faker-kit` (`bin/faker_kit.py`, installed as `~/.hermes/bin/faker-kit`). It prints JSON or lines of text, or `faker-kit: refused: <why>` and exits 1.

| Command | Who | Does |
|---|---|---|
| `install` | owner's shell | Creates the bootstrap bot and chief, guard settings, webhooks and tunnel from a machine config, and sets each bot's model (see Choose each bot's model) |
| `add-project OWNER/REPO` | bootstrap | Creates or refreshes a project bot: profile, guard, skills, jobs, the andon and bug-report labels, webhook routes (when the machine config is kept) proved by a signed ping (`webhooks_live`, a `ping` per hook; a failing ping puts the owner command in `next`), launch checkout. Safe to rerun |
| `refresh` | chief, bootstrap, owner's shell | Fetches the recorded kit source's newest main and runs `install.sh --no-gateway` on it; the gateway is not restarted. Project bots are refused |
| `launch --brief-file F` | project (bootstrap adds `--project P`) | Starts one cloud session; the brief must start with `/faker-mode` and live in the bot's briefs folder |
| `merge --pr N` | project | The merge gate above |
| `status [--all]` | every bot | Projects, open pull requests with their last update, full head commit, and gate state, andon cords |
| `incidents [--ack KEY]` | project | Polls the default branch's checks, health URLs, and Sentry, PagerDuty, Datadog, and Grafana alerts from `.agents/dispatch.json`; prints the incidents not yet acknowledged and those resolved; run on request, since no job polls it. Alert tokens come only from the bot profile's `.env` (`FAKER_KIT_ALERT_*`), are redacted from output, and alert text taints the turn |
| `issues [--claim N]` | project | Lists open issues whose `faker:fix` label a user with write, maintain, or admin permission added (number, labeler, link only); `--claim N` records that a session was launched |
| `andon [show\|raise\|clear]` | project, bootstrap | The project's cord (project bot) or the machine's (bootstrap) |
| `runner [up\|build]` | owner's shell | Self-hosted CI runner containers |

## Install

On a machine that only runs Hermes, describe it once in a machine config and let one script build it:

```bash
gh repo clone <you>/<kit repository> && cd <kit repository>
cp hermes/templates/machine.yaml .agents/hermes-machine.yaml   # fill it in, commit it
hermes/setup-machine.sh
```

`setup-machine.sh` installs what is missing, asks you to sign in where needed, installs the kit (`install.sh`), runs `faker-kit install`, runs the gateway and the Cloudflare Tunnel as services, and checks the result. Run it again after any change; it changes only what differs. `hermes/setup-machine.sh --help` lists its flags.

By hand:

```bash
git clone https://github.com/tsnAnh/faker-kit.git && cd faker-kit
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

Then set `trusted_authors` and `max_in_flight_total` in `~/.hermes/faker-kit/guard.yaml`, and ask the bootstrap bot for your first project.

### Event-driven wakes

With `webhooks` in the machine config, GitHub calls the machine when a pull request, review, check suite, or status changes, and Hermes wakes the bot's `faker-kit-babysit` at once. Hermes's webhook listener has two routes per project bot, `<bot>-pr` and `<bot>-issues` (which passes only the `labeled` action with the `faker:fix` label). Both fire `faker-kit-babysit` with a prompt made only of event names and numbers, after the job's gate script has run: an event that leaves the `faker-kit status` lines unchanged starts no turn until they change, an issue is listed, or six hours pass with a pull request open, and none starts while no pull request is open. The secret per bot is `FAKER_KIT_WEBHOOK_SECRET_<BOT>` in `~/.hermes/.env`; Hermes refuses any request whose `X-Hub-Signature-256` does not match. A Cloudflare Tunnel publishes only `/p/<bot>/webhooks/<route>` for each project bot, and Hermes answers 404 to a route it does not have.

## CI on this machine

`faker-kit runner up --config <machine.yaml>` runs the GitHub Actions jobs of every repository under `ci_runners` here, one fresh Ubuntu container per job. Workflows ask for them with `runs-on: [self-hosted, faker-kit]`. The image is `hermes/ci-runner/`.

## How cloud agents get the kit

A cloud session starts from a clone of the repository and cannot fetch the kit, and no repository carries a copy. The owner's cloud environment gets it from its setup script and a `SessionStart` hook the script registers, as step 2 of `plugins/trust-ladder/skills/setup-cloud-agents/SKILL.md` describes. `faker-kit launch` never reads the repository for a kit. `install.sh` opens no pull request; its `--ref` is the kit commit this machine installs.

## Layout

| Path | What it is |
|---|---|
| `setup-machine.sh`, `install.sh` | Build a machine from a machine config; fetch the kit at a commit, write the wrapper, refresh every bot |
| `bin/faker_kit.py` | The command |
| `lib/faker_kit_gh.py`, `faker_kit_merge.py`, `faker_kit_andon.py`, `faker_kit_cloud.py`, `faker_kit_env.py` | GitHub REST reads with ETags, the merge decision and its lock, andon records, the `claude --cloud` launcher, profile `.env` reads |
| `lib/faker_kit_incidents.py`, `faker_kit_incident_sources.py`, `faker_kit_issues.py` | Incident polling and its local record, the four alert sources, bug-report labels and the collaborator check |
| `lib/faker_kit_profiles.py`, `faker_kit_machine.py`, `faker_kit_runner.py`, `faker_kit_cron_list.py`, `install_config.py` | Bot profiles and jobs, the machine config, CI runners, the `hermes cron list` parser, Hermes config writes |
| `plugin/faker_kit_guard/` | The Hermes plugin that checks every tool call against `guard.yaml` and `bots.yaml` |
| `profiles/`, `skills/`, `cron/project-jobs.md`, `cron/chief-jobs.md` | Bot prompts and config, the bots' skills, the job definitions |
| `cron/scripts/` | The babysit and loop jobs' gate scripts and `faker_kit_gate.py`, the module they share, which `install.sh` writes into each bot's `scripts/` folder with `lib/faker_kit_cron_list.py` (the `hermes cron list` parser the gate reads the job's last run with); the babysit gate keeps its state (fingerprint, wake time, failed-run retries) in `~/.hermes/faker-kit/gates/<bot>/faker-kit-babysit.json` |
| `templates/` | `guard.yaml` and `machine.yaml` to copy, and the brief templates the dispatch skill fills (`triage-incident-brief.md`, `fix-issue-brief.md`) |
| `e2e/run_dispatch_e2e.py`, `e2e/guard_scenario.py`, `e2e/guard-calls.json`, `e2e/tooling_scenario.py` | The kit's only test for the Hermes side, a stubbed end-to-end run: launch, andon, every merge refusal, migration, CI runner slots, the environment setup hook, the ratchet rule, and every guard verdict through the real plugin. Prints a digest; the same on every run |

Under `~/.hermes/faker-kit/`: `kit/` (the kit at the pinned commit), `guard.yaml` (the policy; you edit it), `bots.yaml` (each bot's role, project, and repository), `briefs/<bot>/` (the only place a project bot may write), `andon/`, `merge/<bot>.lock` (the merge lock), `gates/<bot>/` (what each wake gate last saw and when it last woke the agent), `etag/<bot>/`, and `checkouts/<owner>__<repo>/repo` (the launch checkout every session starts from, trusted once).

## What only you do

- Merge pull requests the gate leaves to you (protected paths, forks).
- Sign `gh` in on the machine once (`gh auth login`); every bot shares that login. No bot ever sees a token in chat.
- Sign Claude Code in on the machine. `faker-kit add-project` and every `faker-kit launch` mark the launch checkout trusted in Claude Code's config, so nobody trusts a folder by hand.

## Troubleshooting

- **"Claude Code asks whether to trust <path>".** faker-kit marks each launch checkout trusted itself, so this means the Claude Code config the launch uses is missing or unreadable, or the launch read another config than the one named in the message. Sign Claude Code in on this machine (with the same `CLAUDE_CONFIG_DIR` the bot uses) and launch again.
- **A Claude launch exits with "Not logged in" or "requires an interactive terminal".** The gateway runs `claude` without your shell. Check that the project bot's `.env` has `CLAUDE_CONFIG_DIR` pointing at your `claude` login, and rerun `faker-kit add-project` from a shell where it is exported.
- **Merge keeps refusing.** `faker-kit status` shows the gate that is closed for each pull request.

## Choose each bot's model

A bot's own turns run on the provider and model in its Hermes profile (`model.provider` and `model.default` in its `config.yaml`). Cloud sessions always run in Claude Code through `claude --cloud`, whatever the bots use. `faker-kit launch` caps a cloud session's effort at `high` (`EFFORT_CEILING` in `lib/faker_kit_cloud.py`): a request for `xhigh` or `max` starts at `high` and the launch output names it in `effort_lowered`, and a launch with no `--effort` starts at `medium` and prints `effort_defaulted`, and one with no `--model` starts on `sonnet` and prints `model_defaulted` (`DEFAULT_EFFORT` and `DEFAULT_MODEL` in the same file). Every launch also passes `--permission-mode auto` (`PERMISSION_MODE`, printed as `permission_mode`), never a bypass flag; whether `--cloud` and the cloud platform honor it, and what happens on a model or organization that disallows Auto, is not verified by the e2e run. For each bot, pick one of these:

| You give | What `faker-kit` writes to the profile |
|---|---|
| Nothing | Nothing. The profile keeps the provider and model it has |
| `--model M` alone, or `model` alone in the machine config | `model.default` only, as before |
| `--provider P --model M`, or `provider` and `model` in the machine config | `model.provider` and `model.default`, and removes `model.base_url` and `model.api_mode` so no stale endpoint is left |
| `--claude-subscription`, or `claude_subscription: true` | Hermes's [Claude Subscription DirectSDK](https://hermes-agent.nousresearch.com/docs/plugins/claude-subscription-directsdk) provider plugin and its provider, `model.default` (`sonnet` unless `--model` names another), and removes `model.base_url` and `model.api_mode`. Each turn runs through the Claude Code CLI logged in on this machine |

The flags apply to the bot the command sets up: the bootstrap bot for `install` (with `--bootstrap-bot`), the project bot for `add-project`. In the machine config they are `bootstrap_provider` and `bootstrap_model`, `chief.provider` and `chief.model`, and `provider` and `model` on each project. A `--provider` or `--model` on `install` replaces the bootstrap pair from the machine config as a whole.

`faker-kit` refuses, before it writes anything, a provider with no model, a provider together with the Claude subscription for the same bot (`claude_subscription: true` covers every bot, so set it to false to give any bot a provider), the subscription provider's own id given as a provider, and a `CHANGE-ME` placeholder. The kit picks no provider, model, or reasoning budget for you: write the provider id as Hermes names it and the model you chose.

`faker-kit` never reads, copies, prints, or asks for a credential. If the provider needs a login or a key, sign in with Hermes's own login for that provider from your own terminal, before you run `install` or `add-project`. Then restart the gateway and quit and reopen Hermes Desktop, and check the bot with `hermes -p <bot> status`.

A new project bot given no flags inherits the Claude subscription from an existing bot that uses it, and so does an existing project bot when you run `add-project` again without flags. To keep a bot on its provider, run `add-project` again with the same `--provider` and `--model` (its printed next step says so), or keep the pair in the machine config.
