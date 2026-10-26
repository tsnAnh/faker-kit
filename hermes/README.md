# faker-kit for Hermes

This module runs faker-kit dispatch on a [Hermes Agent](https://github.com/NousResearch/hermes-agent) machine: one bootstrap bot that sets projects up, one project bot per project that runs the project as its long-lived coordinator chat and starts Claude Code cloud sessions, one pull request per unit, and an optional chief of staff, an engineering lead that routes the owner's intent across projects and owns each part until it merges, on weekday jobs from `cron/chief-jobs.md`. How to use it is in [Dispatch work with Hermes](../docs/how-to/dispatch-with-hermes.md).

It lives outside `plugins/` on purpose. Nothing here loads in a coding agent: plugins are what coding agents load, and this is what the Hermes bots load.

## How it works

The project bot is a model. It follows faker-mode's Orchestrate, Autopilot, Babysit, and Shipping playbooks (the `dispatch` skill points at them): it writes a brief, starts a cloud session with `faker-kit launch --brief-file`, finds the session's work by its pull request, has a separate agent verify it, and merges. A unit has no GitHub issue and no label; the pull request is the only GitHub artifact. The bot's plan lives in its orchestrate store under `~/.hermes/faker-kit/briefs/<bot>/orchestrate/<project>/`.

Code keeps only what the model cannot talk past:

- The guard's hard blocks: secret reads, repository deletion, push to main or force-push, raw `gh pr merge` and the merge API, wiping `/` or the home folder.
- `faker-kit merge --pr N` as the only way to land a pull request. It merges only when every check on the head passed and at least one passing check run on it belongs to a `push` or `pull_request` workflow run of this head in a workflow other than `.github/workflows/claude-review.yml` and `.github/workflows/faker-kit-checks.yml` (found through the head's workflow runs and each check run's check suite, so a check's name proves nothing; a `pull_request` run must come from this pull request's branch in this repository, start after the pull request opened, and be attached to this pull request and its base branch, a `push` run counts only when the head's `.github/workflows` folder equals the default branch's, and a run of any other event never counts), `claude[bot]`'s latest formal review is an approval of exactly the current head with a `Risk: low` or `Risk: medium` line, the `security-review` status on the head succeeded and links a `.github/workflows/claude-review.yml` pull_request run on that head for this pull request, and that run's `security` job finished with conclusion `success` (the run and its jobs are read through `repos/R/actions/runs/<id>` and `.../jobs`; the status only locates the run), the pull request touches none of `.github/workflows/`, `.agents/dispatch.json`, the guard files, or a path in `risk_paths_high` or `forbidden_paths` of `.agents/dispatch.json` on the default branch, and a one-hour window has passed. The first passing call tells the owner before the window starts: `hermes -p <bot> send --to slack:<channel>` when the bot has a `slack_channel`, otherwise a pull request comment that mentions the owner. When that is delivered it records the head and the time, prints one `faker-kit notice:` line ("PR #N merges in 1 hour unless you request changes"), and refuses; when it fails it records nothing and refuses. A later call merges only if the head is unchanged and no person has a standing changes-requested review. A new head restarts the window.
- `faker-kit launch` refuses while an andon cord is pulled.

The andon cord and "a separate agent verified it" are instructions in the skills. The cord is a local record plus an open `agent:andon` issue per repository; the briefs tell cloud agents to check for it before every push.

Each project bot has four agent jobs, all running the `dispatch` skill and delivering to the bot's chat: `faker-kit-babysit` (woken by pull request, review, and check events, hourly fallback), `faker-kit-security` (woken by a push to the default branch, six-hourly fallback; launches a `review-main-security` session on the new commits), `faker-kit-incidents` (every 10 minutes; runs `faker-kit incidents` and launches `triage-incident` for each new incident), and `faker-kit-issues` (woken when a collaborator adds the `faker:fix` label to an issue, hourly fallback; launches one session from `templates/fix-issue-brief.md` per listed issue).

## The command

One command, `faker-kit` (`bin/faker_kit.py`, installed as `~/.hermes/bin/faker-kit`). It prints JSON or lines of text, or `faker-kit: refused: <why>` and exits 1.

| Command | Who | Does |
|---|---|---|
| `install` | owner's shell | Creates the bootstrap bot and chief, guard settings, webhooks and tunnel from a machine config, and sets each bot's model (see Choose each bot's model) |
| `add-project OWNER/REPO` | bootstrap | Creates or refreshes a project bot: profile, guard, skills, jobs, the andon and bug-report labels, webhook routes (when the machine config is kept), launch checkout. Safe to rerun |
| `vendor --project P` | bootstrap | Opens the pull request that copies the pinned kit into the repository's `.agents/faker-kit/` |
| `launch --brief-file F` | project (bootstrap adds `--project P`) | Starts one cloud session; the brief must start with `/faker-mode` and live in the bot's briefs folder |
| `merge --pr N` | project | The merge gate above |
| `status [--all]` | every bot | Projects, open pull requests with their gate state (a window counts as started only when one is stored), andon cords |
| `incidents [--ack KEY]` | project | Polls the default branch's checks, health URLs, and Sentry, PagerDuty, Datadog, and Grafana alerts from `.agents/dispatch.json`; prints the incidents not yet acknowledged and those resolved. Alert tokens come only from the bot profile's `.env` (`FAKER_KIT_ALERT_*`), are redacted from output, and alert text taints the turn |
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

With `webhooks` in the machine config, GitHub calls the machine when a pull request, review, check suite, status, or push changes, and Hermes wakes the bot's `faker-kit-babysit` (or `faker-kit-security` for a push) at once. Hermes's webhook listener has three routes per project bot, `<bot>-pr`, `<bot>-security`, and `<bot>-issues` (which passes only the `labeled` action with the `faker:fix` label). Each fires the named agent job with a prompt made only of event names and numbers. The secret per bot is `FAKER_KIT_WEBHOOK_SECRET_<BOT>` in `~/.hermes/.env`; Hermes refuses any request whose `X-Hub-Signature-256` does not match. A Cloudflare Tunnel publishes only `/p/<bot>/webhooks/<route>` for each project bot, and Hermes answers 404 to a route it does not have.

## CI on this machine

`faker-kit runner up --config <machine.yaml>` runs the GitHub Actions jobs of every repository under `ci_runners` here, one fresh Ubuntu container per job. Workflows ask for them with `runs-on: [self-hosted, faker-kit]`. The image is `hermes/ci-runner/`.

## How cloud agents get the kit

A cloud session starts from a clone of the repository and cannot fetch the kit. `faker-kit vendor` copies the kit at this machine's commit into the repository's `.agents/faker-kit/` in a pull request. A project bot refuses to launch in a repository whose vendored kit is not this machine's. To move to a new kit commit, rerun `install.sh --ref <commit>`, then ask the bootstrap bot to run `faker-kit vendor` for each project, and merge each pull request.

## Review workflow

Every project repository needs `.github/workflows/claude-review.yml` (the trust-ladder template `templates/github/claude-review.yml`, which `setup-faker-kit` installs): on every push to a pull request, the Claude GitHub app comments and submits one formal review of the head whose body ends with `Risk: low|medium|high`, and a second job reviews the diff with the `review-main-security` checklist and sets the `security-review` commit status, linking its own run, then fails when it reported a finding. It needs the Claude GitHub app and a `CLAUDE_CODE_OAUTH_TOKEN` secret. Without both, or if that run's `security` job did not succeed, `faker-kit merge` never opens.

## Layout

| Path | What it is |
|---|---|
| `setup-machine.sh`, `install.sh` | Build a machine from a machine config; fetch the kit at a commit, write the wrapper, refresh every bot |
| `bin/faker_kit.py` | The command |
| `lib/faker_kit_gh.py`, `faker_kit_merge.py`, `faker_kit_andon.py`, `faker_kit_cloud.py`, `faker_kit_env.py` | GitHub REST reads with ETags, the merge decision and window store, andon records, the `claude --cloud` launcher, profile `.env` reads |
| `lib/faker_kit_incidents.py`, `faker_kit_incident_sources.py`, `faker_kit_issues.py` | Incident polling and its local record, the four alert sources, bug-report labels and the collaborator check |
| `lib/faker_kit_profiles.py`, `faker_kit_machine.py`, `faker_kit_runner.py`, `install_config.py` | Bot profiles and jobs, the machine config, CI runners, Hermes config writes |
| `plugin/faker_kit_guard/` | The Hermes plugin that checks every tool call against `guard.yaml` and `bots.yaml` |
| `profiles/`, `skills/`, `cron/project-jobs.md` | Bot prompts and config, the bots' skills, the three job definitions |
| `templates/` | `guard.yaml` and `machine.yaml` to copy, and the brief templates the dispatch skill fills (`triage-incident-brief.md`, `fix-issue-brief.md`) |
| `e2e/run_dispatch_e2e.py`, `e2e/guard_scenario.py`, `e2e/guard-calls.json`, `e2e/tooling_scenario.py` | The kit's only test for the Hermes side, a stubbed end-to-end run: launch, andon, every merge refusal, the window, migration, CI runner slots, the vendoring script and API, the ratchet rule, and every guard verdict through the real plugin. Prints a digest; the same on every run |

Under `~/.hermes/faker-kit/`: `kit/` (the kit at the pinned commit), `guard.yaml` (the policy; you edit it), `bots.yaml` (each bot's role, project, and repository), `briefs/<bot>/` (the only place a project bot may write), `andon/`, `merge/<bot>.json` (merge windows), `etag/<bot>/`, and `checkouts/<owner>__<repo>/repo` (the launch checkout every session starts from, trusted once).

## What only you do

- Merge pull requests the gate refuses (`Risk: high`, forks) and each project's kit-vendoring pull request.
- Sign `gh` in on the machine once (`gh auth login`); every bot shares that login. No bot ever sees a token in chat.
- Sign Claude Code in on the machine, and trust each launch checkout once: `cd <launch_checkout> && claude`, choose yes, then `/exit`.
- Install the Claude GitHub app on each project repository and add the `CLAUDE_CODE_OAUTH_TOKEN` secret.

## Troubleshooting

- **"Claude Code asks whether to trust <path>".** Run `cd <path> && claude`, choose yes, then `/exit`, and launch again.
- **A Claude launch exits with "Not logged in" or "requires an interactive terminal".** The gateway runs `claude` without your shell. Check that the project bot's `.env` has `CLAUDE_CONFIG_DIR` pointing at your `claude` login, and rerun `faker-kit add-project` from a shell where it is exported.
- **"the kit update <pull request> conflicts with its base branch".** Ask the bootstrap bot to run `faker-kit vendor --project <project>`; it rebuilds the pull request on the current branch.
- **Merge keeps refusing.** `faker-kit status` shows the gate that is closed for each pull request.

## Choose each bot's model

A bot's own turns run on the provider and model in its Hermes profile (`model.provider` and `model.default` in its `config.yaml`). Cloud sessions always run in Claude Code through `claude --cloud`, whatever the bots use. For each bot, pick one of these:

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
