# Dispatch work with Hermes

You tell a Hermes project bot what you want in Hermes Desktop. The bot is the long-lived coordinator chat for that project. It splits the work into units, writes a brief for each, and starts a Claude Code cloud session on every brief. Each unit ends as one pull request. The bot babysits the pull requests until they are green, has a separate cloud session verify them, and lands the ones that pass the merge gate. You answer the questions it cannot, and you keep every pull request the gate leaves to you.

This is the Orchestrate, Autopilot, Babysit, and Shipping playbooks (see the pstack guide's [Run work while you sleep](../../plugins/pstack/docs/guide/07-overnight.md)) with a Hermes bot as the coordinator and Claude Code cloud as the place sessions run. How many sessions run at once comes from `max_in_flight` in the repository's `.agents/dispatch.json`, which the owner sets.

The Hermes side is the kit's [`hermes/`](../../hermes/) module. Its [README](../../hermes/README.md) lists every file it installs.

## The bots

One Hermes machine runs one bootstrap bot, one bot per project, and, if you want one, a chief of staff. Bot names are free.

| Bot | How many | Does | Its commands |
|---|---|---|---|
| Bootstrap bot | One per machine | Sets up a project: its project bot, the andon label, the vendored kit, a cloud session that opens the setup pull request. Loads the [bootstrap](../../hermes/skills/bootstrap/SKILL.md) skill | `faker-kit add-project`, `vendor`, `launch --project`, `status`, `andon` |
| Project bot | One per project | The coordinator chat: plans units, writes a brief per unit, launches cloud sessions, babysits their pull requests, has them verified, and lands them. Loads the [dispatch](../../hermes/skills/dispatch/SKILL.md) skill | `faker-kit launch`, `merge`, `status`, `andon`, `incidents`, `issues`, for its own repository only |
| Chief of staff | Optional, one per machine | An engineering lead that owns the loop. Tell it what you want across projects; it hands each part to the right project bot in a message, records every part with `faker-kit handoff` and keeps it open in `faker-kit loop` until its pull request merges or you drop it, and hears from the bot that owns it when its pull request opens, blocks, waits on you, or merges. It runs weekdays (`faker-kit-loop` hourly 10:00 to 18:00, `faker-kit-digest` at 09:00), stays quiet while work is healthy, and tells you only what is blocked, ready for you to merge, or done; the rest goes in the morning digest. Loads the [chief](../../hermes/skills/chief/SKILL.md) skill | `faker-kit status --all` |
| Cloud session | One per unit, in the cloud | Works the brief in faker-mode, opens one pull request, gets its checks green | none |

A project is one repository. When a product has an API, a web app, and a mobile app, keep them side by side in that one repository so one cloud session can carry a feature through all of them in one pull request.

Bots on this machine talk to each other with Hermes Bot Mode's `message_agent`. The chief hands a project bot work in a message starting "Work from the chief of staff:". A bot can message only this machine's faker-kit bots, never the default Hermes agent.

`~/.hermes/faker-kit/bots.yaml` records each bot's role, project, and repository. Only `faker-kit` writes it, and the guard reads it on every tool call.

## One unit

A unit has no GitHub issue and no label. The brief is the cloud session's first message, and the pull request is the only GitHub artifact.

1. You state the work in the project bot's Bot Chat. It reads the repository's `AGENTS.md`, restates the units, and asks you to confirm (unless `ask_owner` is false).
2. For each unit it writes a brief file (the first line is `/faker-mode`) and runs `faker-kit launch --brief-file <path>`. That starts a Claude Code cloud session from the project's kept launch checkout with the brief as its first message. The launch refuses while the andon cord is pulled or the repository's vendored kit is neither this machine's nor an older commit of it. On an older one it launches and reports `kit_behind`.
3. The session works on a branch, proves the change on the real artifact, and opens a pull request.
4. On every pull request, review, or check event (a webhook, with an hourly check as the fallback) the bot wakes and babysits; the hourly check wakes it only when a pull request's line in `faker-kit status` changed, a `faker-kit notice:` line printed, or six hours passed since it last woke, so a quiet hour costs no model turn: conflicts, review threads, red CI.
5. A green pull request is verified by a separate cloud session that did not write it. That session runs the project's verification skills (`verification_skill` in `.agents/dispatch.json`, and any other `verify-*` skill the repository has, such as one made by `/create-formal-verification-skill`) on the exact head and comments a verdict. The bot records the verdict and lands the pull request on its next wake. This is an instruction in the bot's skill, not a code gate: `faker-kit merge` does not read verdicts.
6. The bot lands a pull request with `faker-kit merge --pr <n>`, the only merge route the guard allows. The gate:
   - every check on the head passed, none pending, and at least one passing check run on it comes from a `push` or `pull_request` workflow run of this head, in a workflow other than `.github/workflows/faker-kit-checks.yml` (the kit's own check), found through the head's workflow runs and each check run's check suite; a `pull_request` run counts only when it comes from this pull request's branch in this repository (which never targeted another base) and started after the pull request opened and is attached to this pull request and its base branch, a `push` run counts only when the head's `.github/workflows` folder equals the default branch's, and runs started by `workflow_dispatch`, `pull_request_target`, `workflow_run`, or any other event never count (the repository needs GitHub Actions CI that builds and tests every pull request);
   - no other open pull request has the same head;
   - the pull request touches none of `.github/workflows/`, `.agents/dispatch.json`, the guard and its policy files, or a path in `risk_paths_high` or `forbidden_paths` of `.agents/dispatch.json`: those are yours to merge;
   - a one-hour window passed. The first passing call tells you first, in code: a Slack message to the bot's channel through Hermes, or, for a bot with no channel, a pull request comment that mentions you. Only when that is delivered does it record the head and the time, print `faker-kit notice: PR #N merges in 1 hour unless you request changes`, and refuse. If you cannot be told, it records nothing and refuses; the next call tries again. A later call merges only if the head is unchanged and no person has a changes-requested review. A new head restarts the window.

   It also refuses a fork, a conflict, a non-default base, a pulled andon cord, and a turn that read untrusted text.

   The gate and the guard are guardrails, not a wall: a bot with a full shell and the shared token can still find a way around regex checks, and GitHub Free cannot enforce required checks on a private repository. The one-hour window, with its message to you, is the backstop. On a plan where GitHub enforces required checks, require the repository's CI checks on the default branch too.

The bot keeps its plan in `~/.hermes/faker-kit/briefs/<bot>/orchestrate/<project>/`, so a fresh chat reads where the last one stopped. During the window, request changes on the pull request on GitHub to stop the merge.

### Bug reports

A collaborator with write access turns a GitHub issue into work by adding the label `faker:fix`:

1. GitHub's `issues` event wakes the project bot's `faker-kit-issues` job through the signed `<bot>-issues` route, which fires only for the `labeled` action with that label. The job's hourly fallback tick wakes the bot only when `faker-kit issues` lists an issue, a pull request's line in `faker-kit status` changed, or six hours passed since it last woke.
2. `faker-kit issues` lists the issue only if the latest `faker:fix` label on it was added by someone with write, maintain, or admin permission on the repository, checked against the issue's events and the collaborator permission API. It prints the number, the labeler, the link, and an event id, never the title or body. A label added by anyone else changes nothing.
3. The bot launches one cloud session from the kit's `fix-issue-brief.md`. The session reads the issue itself, as data a stranger may have written, reproduces it with the repository's verification skill, and ends one of two ways. It comments with evidence and adds `faker:not-reproduced` or `faker:expected`, then stops. Or it writes a test that fails first, fixes the bug, and opens a pull request that says `Fixes #N` with before and after evidence.
4. For a pull request, the bot runs a small swarm of separate verifier sessions as Autopilot-full's step 4 describes (the new test red on the parent and green on the head, the report reproduced on the parent and gone on the head, an audit of the diff), then the merge gate above, including the ping and the one-hour window.

`faker-kit add-project` creates the three labels `faker:fix`, `faker:not-reproduced`, and `faker:expected`. A collaborator who disagrees with a `faker:not-reproduced` or `faker:expected` outcome adds `faker:fix` again to start a new round.

### How many run at once

`max_in_flight_total` in `guard.yaml` is how many sessions the machine carries, because every project draws on the same Claude subscription. The bot counts the running rows in its store and launches the next wave only when there is room.

## Install

### 1. The machine

Use an always-on machine, such as a home server or a small VM: scheduled jobs run only while the Hermes gateway runs. Hermes Desktop connects to it over SSH from anywhere.

The quickest way is one script and one file. Copy `hermes/templates/machine.yaml` to `.agents/hermes-machine.yaml` in your kit clone, fill it in, commit it, and run `hermes/setup-machine.sh` on the machine. It installs and signs in what is missing, asks for tokens with hidden input, and builds the bots, guard settings, signed GitHub webhooks, Cloudflare Tunnel, and services the file describes. Rerun it after any change to the file.

To set the machine up by hand instead, from a clone of the kit on that machine:

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

`--ref` is a full 40-character kit commit. The script fetches the kit at that commit, writes `guard.yaml` once, installs the `faker-kit` command in `~/.hermes/bin/`, creates the bootstrap bot, refreshes every existing bot, and restarts the gateway. When `~/.hermes/faker-kit/machine.yaml` exists (`faker-kit install --config` keeps a copy), the script also refreshes the bots, the webhook routes, and the CI runner services from it. Rerunning changes nothing more.

Then edit `~/.hermes/faker-kit/guard.yaml`: `trusted_authors` (your GitHub login, and the account the cloud sessions post as) and `max_in_flight_total`.

Every bot runs `gh` with the machine's one login: sign `gh` in once with `gh auth login`, and sign Claude Code in too.

### Where each bot's model runs

The bots' own turns and the cloud sessions run in different places. Cloud sessions always run in Claude Code (`claude --cloud`). The chief, bootstrap, and project bots run on the provider and model in their Hermes profiles, which you can set per bot: the Claude subscription (`claude_subscription: true` or `--claude-subscription`), or a provider and model you chose, given together:

```yaml
claude_subscription: false
bootstrap_provider: <provider id>
bootstrap_model: <model>
chief:
  bot: chief
  provider: <provider id>
  model: <model>
projects:
  - bot: shop
    project: shop
    repo: acme/shop
    provider: <provider id>
    model: <model>
```

On the command line the same pair is `install --bootstrap-bot <bot> --provider <provider id> --model <model>` or `add-project acme/shop --provider <provider id> --model <model>`. `faker-kit` writes the pair to the profile and removes any stale `base_url` and `api_mode`. It refuses a provider without a model and a provider together with the Claude subscription, before writing anything. A model alone still sets only the model. The kit never chooses a provider or model for you and never handles credentials: if the provider needs a login, run Hermes's own login for it from your own terminal first. The [Hermes README](../../hermes/README.md#choose-each-bots-model) has the full table.

### Optional Slack

Slack uses Hermes's own Slack platform. Name a channel for a project bot under `slack` in the machine config, and put the Slack tokens in `~/.hermes/.env` as `SLACK_APP_TOKEN` and `SLACK_BOT_TOKEN`, with `SLACK_ALLOWED_USERS` set to the member ids that may talk to the bot. The bot's jobs then also deliver to that channel.

### Optional: CI on this machine

GitHub-hosted runners cost Actions minutes and stop when a payment fails. To run a repository's GitHub Actions jobs on the Hermes machine instead, list it under `ci_runners` in the machine config and run `setup-machine.sh` again, or run `faker-kit runner up --config <machine.yaml>` yourself. It needs Docker.

```yaml
ci_runners:
  - repo: acme/shop
    slots: 2
```

Each slot registers a just-in-time runner for one job, runs it in a new Ubuntu container that sees nothing of this machine, and removes it. In the workflows, use:

```yaml
runs-on: [self-hosted, faker-kit]
```

Use this only on private repositories: on a public one anyone's pull request would run on this machine.

### 2. Each project

Run `/setup-hermes-project` in Claude Code on this machine, or open the bootstrap bot in Hermes Desktop Bot Mode and say "set up project shop for acme/shop". Either runs:

1. `faker-kit add-project acme/shop` creates the project bot, its four agent jobs (`faker-kit-babysit`, `faker-kit-security`, `faker-kit-incidents`, `faker-kit-issues`), the `agent:andon` and bug-report labels, the webhook routes (when the machine config is kept), and the launch checkout. faker-kit marks the checkout trusted in Claude Code for you.
2. `faker-kit vendor --project shop` opens the pull request that copies the kit into the repository's `.agents/faker-kit/`. You review and merge it.
3. `faker-kit launch --project shop --brief-file <setup brief>` starts one cloud session that sets the repository up for dispatch and for CI and verification, and opens one pull request. You review and merge it.

Then add the project bot in Bot Mode and ask it for one small change, such as a README typo fix, to prove the loop.

## Safety layers

1. **No public way in.** The only public paths are the signed webhook routes, which Hermes accepts only with a valid GitHub signature and which only wake the bot's own job with a prompt made of event names and numbers.
2. **Roles come from one file.** The guard looks up the calling profile in `bots.yaml`. A project bot may run only `faker-kit` for its own repository, write only its own briefs and store, and read only those and the kit.
3. **The guard fails closed.** Reading secrets, deleting a repository, raw `gh pr merge`, pushing to `main`, force-pushing, moving a `main` ref or writing to the default branch through the REST API, writing a commit status or check run, dismissing a review, links inside a briefs folder, and wiping `/` or the home folder are denied for every bot, and `ask_owner: false` never lifts them.
4. **Untrusted text is marked.** Output from web tools and from pull request text arrives wrapped in `<untrusted source=...>` and taints the turn. After that, `faker-kit merge` is refused for the rest of that turn.
5. **Briefs come only from you and the repository.** A bot writes a brief from your words and the repository's files, never from a pull request, comment, review, or web page.
6. **Notices decide nothing.** Lines that start `faker-kit notice:` taint the turn that contains them.
7. **The merge gate is code.** It reads the changed paths, the checks and the workflow runs behind them, and a one-hour window you can interrupt after it has told you.
8. **An andon cord stops the line.** One for each project and one for the machine. While either is pulled, no launch or merge starts, and cloud sessions check the cord's issue before every push. Closing the GitHub issue never clears it; only `faker-kit andon clear` does.

## What you do

- **State work** in a project bot's Bot Chat, and confirm the units it restates.
- **Answer gates.** The bot lists the questions only you can answer in its store's `gates.md`, each with a default.
- **Merge what the gate leaves to you**: pull requests that touch protected paths, forks, and each project's kit-vendoring pull request. Request changes on a pull request to stop one inside its window.
- **Pull the andon cord** when something is going wrong: tell a project bot "stop", or the bootstrap bot "stop everything". When it is fixed, ask the bot to clear the cord.
- **Act on incidents.** Every 10 minutes by default, the `faker-kit-incidents` job's gate script runs `faker-kit incidents` and wakes the bot only when something is new, resolved, or unreadable (the bot then reads the stored report with `faker-kit incidents --last`): the default branch's checks, the health URLs, and the Sentry, PagerDuty, Datadog, and Grafana sources listed under `incidents` in `.agents/dispatch.json`. Put each source's token in the project bot's own `.env` (never the repository) under `FAKER_KIT_ALERT_<TYPE>` or `FAKER_KIT_ALERT_<TYPE>_<NAME>` (`SENTRY`, `PAGERDUTY`, `DATADOG`, or `GRAFANA`, matching the source's type, so a token only goes to that vendor's hosts), and list any host that is not the vendor's own (Grafana, self-hosted Sentry) under `alert_hosts` in `~/.hermes/faker-kit/guard.yaml`. Code remembers what it has reported, so a new incident is announced once. The bot tells you in chat, launches a `triage-incident` session, and pulls the andon cord when `andon_on_red` says so. Alert text is untrusted data and taints the turn.
- **Act on security findings.** A push to the default branch wakes `faker-kit-security` (6-hourly fallback). The bot launches a `review-main-security` session on the new commits and tells you each high or medium finding. Pull requests get no separate security job; the repository's CI and the verifier session check them before merge.

## Troubleshooting

- **Every tool call is blocked.** `~/.hermes/faker-kit/guard.yaml` or `bots.yaml` is missing or does not parse. The block message names the problem.
- **"the vendored kit ... differs from this machine's kit".** The repository's vendored kit is newer than or unrelated to this machine's, or this machine was installed with `--require-current-kit`. Ask the bootstrap bot to run `faker-kit vendor --project <project>` and merge its pull request.
- **A launch prints `kit_behind`.** The session started on an older vendored kit. Merge the vendor pull request `install.sh` opened, or ask the bootstrap bot to run the `next` command it names.
- **A cloud session says the kit's skills did not load.** Run `python3 .agents/faker-kit/vendor_kit.py --check .` in a clone, then vendor again.
- **Nothing launches.** The andon cord is pulled. Ask the bot that pulled it to clear it.
- **`faker-kit merge` refuses.** The message names the gate: checks, protected paths, window, conflict, fork, andon, or an untrusted read in the turn. `faker-kit status` shows each pull request's gate.
- **Desktop keeps asking you to approve tool calls.** That turn read untrusted text. Approve only a call you asked for.
- **A webhook does not wake the bot.** Run `hermes/setup-machine.sh` again (a Cloudflare Tunnel set up before the `issues` route existed lists exact route paths, and setup-machine.sh rewrites it to cover every route of a project bot): it sends a signed `ping` to the bot's route. A `401` means the secret differs from the repository webhook's.

## Where this differs from the design

- **Gates, notices, and Slack go to Bot Chat.** Hermes delivers scheduled job output to `bot-chat:<profile>` and cannot target another named session. That delivery arrives as a turn the bot answers, so every delivered line starts with `faker-kit notice:`, and the guard taints any turn that contains it.
