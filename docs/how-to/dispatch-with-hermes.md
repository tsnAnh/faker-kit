# Dispatch work with Hermes

You tell a Hermes bot what you want in Hermes Desktop. It splits the work into units, writes a brief for each, and starts a Claude Code or Codex cloud agent on it. Every unit ends as one pull request with passing checks. You answer the questions agents cannot, and you merge. Nothing else needs you.

This is the "cloud sessions, one pull request each" level of [Run many agents](run-many-agents.md), driven from chat. How many units run at once comes from the repository's `/trust-audit` score. A program that needs a coordinator running dozens of agents for days is the job of the Orchestrate playbook, or of trust-ladder's `/orchestrate` across cloud sessions.

The Hermes side is the kit's [`hermes/`](../../hermes/) module. Its [README](../../hermes/README.md) lists every file it installs.

## The bots

One Hermes machine runs one bootstrap bot, one bot per project, and, if you want one, a chief of staff. Bot names are free: `ong-xay` and `meo-beo` work as well as `bootstrap` and `shop`.

| Bot | How many | Does | Its only command |
|---|---|---|---|
| Bootstrap bot | One per machine | Sets up a project: its project bot, the repository's labels, the vendored kit, a cloud session that opens the setup pull request, branch protection. Loads the [bootstrap](../../hermes/skills/bootstrap/SKILL.md) skill. You approve every command it runs but `status` | `team-kit-admin` |
| Project bot | One per project | Plans units, writes a brief per unit, dispatches it. Loads the [dispatch](../../hermes/skills/dispatch/SKILL.md) skill | `team-kit-gh`, for its own repository only |
| Chief of staff | Optional, one per machine | Reads every project. Tell it what you want across projects; it says which project bot gets which part, in what order, and hands each part straight to that project bot in a message. You never copy and paste between bots. Ask it "what's going on" for one line per project. Loads the [chief](../../hermes/skills/chief/SKILL.md) skill | `team-kit-chief` |
| Coding agent | One per unit, in the cloud | Works the brief in faker-mode with the [follow-brief](../../plugins/trust-ladder/skills/follow-brief/SKILL.md) skill, opens one pull request, gets its checks green, posts a report | — |

A project is one repository. When a product has an API, a web app, and a mobile app, keep them side by side in that one repository, such as `apps/api/`, `apps/web/`, and `apps/mobile/` in `acme/shop`. Then one cloud agent can carry a feature through all of them in one pull request and prove it end to end, which separate repositories cannot do. The kit's playbooks assume one repository too. A project bot sees only its repository, so brainstorming one project never pulls in another's context. To brainstorm across projects, add several project bots to a group chat. There they only talk; each bot does work only when you ask it in its own Bot Chat. To route one intent across projects, ask the chief of staff instead: ask the bootstrap bot for one (`team-kit-admin add-chief`), and give it a GitHub token that can only read the projects' repositories. When one local project must wait for another local project's merged unit, the downstream brief may depend on `OWNER/REPO#n`; the downstream bot reads only the upstream bot's published unit-state file on the Hermes machine.

Bots on this machine talk to each other with Hermes Bot Mode's `message_agent`. The chief asks a project bot for the state of its code (the project bot answers from `team-kit-gh code` and its unit commands) and hands it work in a message starting "Work from the chief of staff:". The project bot treats that message as data: it restates the work as units, briefs, and dispatches them, then replies with the issue numbers. With `ask_owner: false` it does this without asking; otherwise it asks you to confirm the units in its own Bot Chat. A bot can message only this machine's team-kit bots, never the default Hermes agent. If a delivery fails because the other bot is busy, the sender tries once more.

The bot that maintains the kit itself is not part of this module. Keep using whatever you use for it.

`~/.hermes/team-kit/bots.yaml` records each bot's role, project, and repository. Only `team-kit-admin` writes it, and the guard reads it on every tool call.

## One unit

1. You state the work in the project bot's Bot Chat. It reads the repository's `AGENTS.md`, restates the units, and asks you to confirm. A feature that touches the API, web, and mobile is usually one unit.
2. Per unit, it writes the brief and runs `team-kit-gh dispatch`. That opens the issue, labeled `agent-task`, then launches the cloud agent and labels the issue `agent:running`. A unit whose dependencies have not merged, or that has no free slot, stays queued.
3. The coding agent checks the issue came from a trusted login with the brief marker, does the work, opens a pull request that closes the issue, and comments a report.
4. Every 30 minutes by default, or on the interval set with `team-kit-admin add-project --tick-every`, the bot's `team-kit-tick` job runs `team-kit-gh tick`, which is code, not a model:
   - a `done` report whose pull request passed its checks starts a verifier: a separate cloud session, on `verifier_backend`, that did not write the change. It checks out the pull request with the `verify-unit` skill, proves each Acceptance line on the real app, and comments a verdict. The unit is labeled `agent:verifying` and keeps its slot;
   - the verifier signs its verdict with a one-use key that only your machine and its session hold, so the coding agent cannot post one for it. A signed verdict listed in `accept_verifications` (by default `live-ui-verified` or `unit-test-verified`) moves the unit to `agent:review` once the checks still pass on the same head, and you get a "ready to merge" line. Any other verdict, a pull request that moved during verification, or no signed verdict within a day opens a gate. `team-kit-gh verify --issue N` (ask the bot) checks the current head again without redoing the coding work; `launch` redoes both;
   - a `partial` or `blocked` report, failed checks, no pull request, or no report within a day opens a gate (`agent:needs-human`), and you get a line saying why;
   - a done report whose checks are still running waits for them;
   - either way the slot frees, and queued units that can start now are launched.
5. You review and merge. The merge closes the issue, which releases the units that depend on it, including units in other local projects that name it as `OWNER/REPO#n`.

The issue's labels, and whether it is closed as completed, are the unit's whole state, so GitHub's issue list filtered by label is your board.

An open issue labelled `agent:proposal` is not a unit. It is a teammate proposal. Only a collaborator's proposal counts (the author has write, maintain, or admin permission, or is the repository's owner or a collaborator; a member counts only when the permission check confirms write, maintain, or admin, since an organization member can have read-only access); others are ignored, and each ignored issue is checked once. `team-kit-gh teammate-proposals` lists its number, author, title, and link, and `team-kit-tick` sends one notice per proposal with only the number, author, and link, never the title. An issue that also carries `agent-task` is a unit, and the proposal label is ignored. The project bot does not read the proposal body, comments, or attached discussion while deciding what to do. If you want the proposal done, restate the desired work in the project bot's own Bot Chat in Hermes Desktop before it writes a brief or dispatches anything.

Each repository picks its coding agent once, in `.agents/dispatch.json` (`backend` is `claude` or `codex`). A launch does one of these. `N` is 1, 2, or 3 for a small, medium, or large unit.

```bash
claude --cloud <task>                            # creates the session, on a terminal
claude --cloud <session> -p                      # signing key on stdin, only for keyed sessions
codex cloud exec --env ENV_ID --attempts N -   # prompt on stdin
```

The prompt always starts with `/faker-mode` or `$faker-mode`. The `faker-mode` skill cannot load itself, so the prompt has to run it as a command. The kit never puts a signing key in process arguments or URLs, so one-use keys for verifier, PR review, security, and incident sessions stay out of process listings. For `claude`, Hermes creates the session from the project's kept launch checkout, which you trust once, on a pseudo-terminal, with the task as its first message: the CLI takes a new session's first message only as an argument. A keyed task names where its key comes instead of the key, and Hermes sends the key alone into that session on stdin with `claude --cloud <session> -p`. A coding unit's task holds no key, so it is the only message. The sessions show up with your other Claude Code sessions, and the call goes out from your machine, so nothing needs a public address. For `codex`, Hermes runs the command on a pseudo-terminal in an empty directory, strips the terminal escape sequences, and reads the task id from what it prints.

### When work splits into several units

A unit is one pull request. The bot splits work only when one pull request would be too big to review, so it goes in steps, or when parts are independent and can run in parallel. Each brief's Depends on section lists one issue per line: `#n` for its own repository, or `OWNER/REPO#n` for another project registered in this machine's `bots.yaml`. A unit launches only after each of them is closed by a merged pull request, so every agent starts from code that is already on the default branch. For cross-project work, dispatch the provider unit first so the downstream brief can name its issue; the downstream unit can queue immediately and will launch after the provider merges.

### How the agent proves its work

The agent drives the real app with the repository's verification skill, named as `verification_skill` in `.agents/dispatch.json` and in each brief's Verify section, and puts the evidence in its report. Then a second session checks it: the agent that wrote a change never grades it. Set `verifier_backend` to the other agent from `backend` when you have both, so a second model family looks at every change; `none` turns verification off. A `codex` verifier needs `codex_env`, and its environment must let tasks comment on issues. Without a verification skill both prove the change with tests only, which reaches `unit-test-verified` at most; the setup pull request then lists `/create-verification-skill` for you. A cloud session runs on Linux, so iOS builds cannot run there; the repository's `AGENTS.md` says what a cloud session cannot prove.

### Codex tasks that finish without a signed comment

A Codex cloud task reports the same way a Claude Code session does: by commenting on the issue with the `team-kit` header. The Codex command line can say whether a task has finished, but it has no way to read what the task answered, so Hermes never tries to read a final message.

Each tick, for a Codex coding, verifier, security, or triage task that has not yet commented, Hermes runs `codex cloud status <task id>` on the machine that runs the bot and reads only the state in brackets at the start of its first line (`PENDING`, `READY`, `APPLIED`, or `ERROR`). Nothing else the command prints is used, and it is never put in a notice. If the task has finished (`READY` or `APPLIED`) or failed (`ERROR`), Hermes reads the issue once more in case the comment arrived while it asked, and then, with still no signed comment, opens the usual gate or sends the notice at once. It does not wait a day.

When the state cannot be read, whether `codex` is not installed, not signed in, too slow, or prints something else, the one-day deadline applies as before. To see what your installed version prints, run `codex --version` and `codex cloud status --help`, and try `codex cloud status <task id>` on a task you started.

### How many run at once

Two limits apply to every launch, and both count open issues labeled `agent:running`:

- `max_in_flight` in the repository's `.agents/dispatch.json` caps the project. The setup session sets it from `/trust-audit`: 1 while any layer is below 2, more once every layer is at 2 or above. Raise it in a pull request as the score improves.
- `max_in_flight_total` in `guard.yaml` caps the whole machine, because every project draws on the same Claude or ChatGPT subscription. Each project bot's GitHub login sees only its own repository, so each bot records its count in `~/.hermes/team-kit/in-flight/` and reads the others' from there.

A unit over either limit stays queued, and the next tick launches it when a slot frees up, oldest first. A unit frees its slot once its agent has reported and its checks have finished, not when you merge.

Each tick also records that project's open and closed unit issues in `~/.hermes/team-kit/units/<bot>.json`. Other project bots use those files for `OWNER/REPO#n` dependencies instead of using their GitHub tokens on another repository. The file lists the 200 newest closed units plus a `wants` list of the other-project issues that bot's queued briefs depend on; the owning bot publishes every issue another bot wants, whatever its age, from its next tick. A dependency on a plain issue (no `agent-task` label) is never a unit, so `dispatch` refuses it and tick gates a unit that names one. If the file is missing, malformed, or older than 24 hours, the downstream unit stays queued with a reason until the upstream bot's next healthy tick.

## Install

### 1. The machine

Use an always-on machine, such as a home server, a Raspberry Pi, or a small VM: scheduled jobs run only while the Hermes gateway runs. Hermes Desktop connects to it over SSH from anywhere.

The quickest way is one script and one file. Copy `hermes/templates/machine.yaml` to `.agents/hermes-machine.yaml` in your kit clone, fill it in, commit it, and run `hermes/setup-machine.sh` on the machine. It installs and signs in what is missing, asks for tokens with hidden input, and builds the bots, guard settings, signed GitHub webhooks, Cloudflare Tunnel, Slack relay, and services the file describes. With `webhooks` set, GitHub events start units within seconds instead of at the next tick. Rerun it after any change to the file. See `hermes/README.md`, Install, for what each part does.

To set the machine up by hand instead, from a clone of the kit on that machine:

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

`--ref` is a full 40-character kit commit. Every repository's `kit_ref` must equal it. The script fetches the kit at that commit, writes `guard.yaml` once, installs `team-kit-gh`, `team-kit-admin`, `team-kit-chief`, and `team-kit-slack` in `~/.hermes/bin/`, creates the bootstrap bot, refreshes every existing bot from the new kit, and restarts the gateway. Run it again with a new commit to move every bot to it, then ask the bootstrap bot to vendor the new commit into each project's repository (below).

Then edit `~/.hermes/team-kit/guard.yaml`:

- `trusted_authors`: your GitHub login, and each project bot's login. Reports by anyone else are ignored, so a copied report cannot move a unit.
- `max_in_flight_total`: how many cloud sessions may run at once on this machine.

Every bot runs `gh` with the machine's one login: sign `gh` in once with `gh auth login`. A bot needs a `GH_TOKEN` in its profile's `.env` only to act as a different GitHub account. Sign in the coding agent too: `claude` for Claude Code cloud, `codex` for Codex cloud.

The installer works with macOS's bundled Bash 3. It needs a Python 3.9+ that can import PyYAML: it tries the Python in Hermes's shebang, then `python3` on `PATH`. Otherwise, install PyYAML in a helper Python and pass it as `HERMES_PYTHON`. Hermes configuration is changed only through `hermes config set` and read back.

### Optional Slack relay

Slack is a relay, not an authority. It opens one outbound Socket Mode WebSocket to Slack, listens only for app mentions in one configured channel, and delivers them to one project bot's Bot Chat as notices like:

```text
team-kit notice: proposal from @x in #faker-kit: <untrusted source="slack:C123...">...</untrusted>
```

The notice taints the turn. The bot must not dispatch from it, and any side-effecting tool call would ask for your approval. To turn it on:

1. In `~/.hermes/team-kit/guard.yaml`, set the `slack` block:

   ```yaml
   slack:
     enabled: true
     bot: meo-beo
     channel_id: C0123456789
     channel_name: faker-kit
     app_token_env: TEAM_KIT_SLACK_APP_TOKEN
     bot_token_env: TEAM_KIT_SLACK_BOT_TOKEN
     mirror_notices: false
   ```

   `channel_id` is the Slack channel id. `channel_name` is the display name used in notices. The token fields are variable names only; the guard rejects token-looking values.

2. Put the token values in the configured bot profile's `.env`, not in chat or `guard.yaml`:

   ```bash
   printf '%s\n' \
     'TEAM_KIT_SLACK_APP_TOKEN=<app-level-token>' \
     'TEAM_KIT_SLACK_BOT_TOKEN=<bot-token>' \
     >> ~/.hermes/profiles/meo-beo/.env
   chmod 600 ~/.hermes/profiles/meo-beo/.env
   ```

   The app token needs Socket Mode. The bot token is needed only for optional outbound mirroring with `mirror_notices: true`.

3. Validate without network:

   ```bash
   ~/.hermes/bin/team-kit-slack check
   ```

4. Install a user service. On Linux with systemd user services:

   ```bash
   mkdir -p ~/.config/systemd/user
   ~/.hermes/bin/team-kit-slack service --format systemd > ~/.config/systemd/user/team-kit-slack.service
   systemctl --user daemon-reload
   systemctl --user enable --now team-kit-slack.service
   ```

   On macOS with launchd:

   ```bash
   mkdir -p ~/Library/LaunchAgents ~/.hermes/team-kit/logs
   ~/.hermes/bin/team-kit-slack service --format launchd > ~/Library/LaunchAgents/team-kit.slack.plist
   launchctl load -w ~/Library/LaunchAgents/team-kit.slack.plist
   ```

There is no public callback URL, inbound listener, webhook secret, or Slack text on a command line. To optionally copy Hermes notices back to Slack, enable `mirror_notices` and pipe a notice file through `team-kit-slack mirror --file <path>` from an operator-controlled script.

### Optional: CI on this machine

GitHub-hosted runners cost Actions minutes and stop when a payment fails. To run a repository's GitHub Actions jobs on the Hermes machine instead, list it under `ci_runners` in the machine config and run `setup-machine.sh` again, or run `team-kit-runner up --config <machine.yaml>` yourself. It needs Docker (Docker Desktop on macOS).

```yaml
ci_runners:
  - repo: acme/shop
    slots: 2        # jobs at once, 1 to 8
```

Each slot is a background service (a launchd agent on macOS, a systemd user unit on Linux) that registers a just-in-time runner for one job, runs it in a new Ubuntu container, and removes it. The container sees nothing of this machine: no Hermes files, no `gh` login, no bot secrets. The runner's one-job configuration reaches it on stdin, never in an argument, the environment, or a file on this machine. In the workflows, use:

```yaml
runs-on: [self-hosted, team-kit]
```

A job that needs Docker itself, such as building and pushing an image, cannot run in that container; keep it on `ubuntu-latest`. Each slot logs to `~/.hermes/team-kit/logs/team-kit-ci-<repo>-<n>.log`. Self-hosted runners are safe here only because the repositories are private: on a public repository anyone's pull request would run on this machine.

### 2. Each project

The quickest way is your own coding agent: run `/setup-hermes-project` (or `$setup-hermes-project` in Codex) in Claude Code, Codex, or Pi on this machine. It asks a few questions and runs every step below, stopping where you trust the launch checkout or paste a token in your own terminal, or merge a pull request. The bootstrap bot does the same from Hermes Desktop:

Open the bootstrap bot in Hermes Desktop Bot Mode and say "set up project shop for acme/shop". It asks for the bot's name and the coding agent, then, with your approval at each step:

1. `team-kit-admin add-project` creates the project bot and its five jobs, paused, and the eleven labels in the repository. Add `--tick-every 10m` or `--incidents-every 5m` when a project needs lower-latency polling than the defaults, `30m` for ticks and `10m` for incidents.
   It also clones the project's launch checkout and prints its path; for a `claude` repository, trust it once in your own terminal (`cd <path> && claude`, choose yes, then `/exit`). Run it with `CLAUDE_CONFIG_DIR` exported if your `claude` login uses one: the Hermes gateway does not inherit your shell, so it records that in the bot's `.env`.
   Add `--claude-subscription` to run the project bot's own model on your Claude subscription through the Claude Code CLI logged in on this machine, with no API key; see the [Hermes README](../../hermes/README.md#run-bots-on-your-claude-subscription).
2. `team-kit-admin vendor` opens a pull request that copies the kit, at the machine's commit, into the repository's `.agents/team-kit/` and links its skills into `.claude/skills/` and `.agents/skills/`. Cloud sessions start from a clone of the repository, and cannot install plugins or fetch a private kit, so this copy is where they get the kit's skills. The repository's own skills keep their place. You review and merge it.
3. `team-kit-admin setup-repo` starts one cloud session. It runs faker-mode and the [setup-dispatch](../../plugins/trust-ladder/skills/setup-dispatch/SKILL.md) skill, scores the repository with `/trust-audit`, and opens one pull request: `.agents/dispatch.json` (with the `verification_skill`), `.agents/dispatch.md`, a Layout section in `AGENTS.md`, and any kit setup the repository lacks. You review and merge it.
4. `team-kit-admin protect` makes the CI check that runs `scripts/check.sh` required on the default branch.
5. No token step: the project bot shares the machine's `gh` login. Give it a `GH_TOKEN` in `~/.hermes/profiles/<project bot>/.env` only to make it a separate GitHub account. Never in chat.
6. `team-kit-admin activate` resumes the project bot's jobs.

Then add the project bot in Bot Mode and ask it for one small change, such as a README typo fix, to prove the loop.

## Safety layers

None of these uses a model to decide. Each is code or configuration.

1. **No public way in, and Desktop is the only way to give work.** Hermes needs no inbound route, public callback, tunnel, or webhook, and the machine listens to nothing from outside. The optional Slack relay is outbound-only Socket Mode and produces untrusted notices, not commands.
2. **Roles come from one file.** The guard looks up the calling profile in `bots.yaml`. A profile that is not there gets nothing. Each role has a fixed list of tools, whatever its toolsets say. A project bot may run only `team-kit-gh`, write only its own briefs, and read only its briefs and the kit, so it cannot read a token or another bot's files. The bootstrap bot may run only `team-kit-admin`, and every command but `status` waits for your approval. The chief may run only `team-kit-chief` and message other team-kit bots; its one write, `new-project`, waits for your approval.
3. **Projects are walled off.** `team-kit-gh` works only on the bot's own repository. The bots share the machine's `gh` login, so the wall is in `team-kit-gh` and the guard, not in the token; give a bot its own narrow `GH_TOKEN` to wall it off at GitHub too. Cross-project Depends on refs are allowed only for repositories owned by another project bot in local `bots.yaml`, and they are resolved from `~/.hermes/team-kit/units/<bot>.json`, not from the other repository's API.
4. **The guard fails closed.** The `team_kit_guard` plugin checks every tool call against `guard.yaml` and `bots.yaml`. Merges, pushes to `main`, force-pushes, and deletes are denied for every bot. Any error, a timeout, or a missing or invalid file blocks the call.
5. **Untrusted text is marked.** Output from `team-kit-gh read`, web tools, and optional Slack proposals arrives wrapped in `<untrusted source=...>` and taints the turn. After that, every tool that changes something asks you to approve it in Desktop. To stop every question, set `ask_owner: false` in `guard.yaml` and run `team-kit-admin sync`: the guard stops asking, each bot's Hermes `approvals.mode` becomes `off`, project bots dispatch the units they split without asking you to confirm, and work the chief hands over by message is briefed and dispatched as soon as it arrives. Deny rules, role limits, and the `<untrusted>` labels still apply, but an injected instruction in untrusted text can then make a bot act within its role without you seeing it first.
6. **Briefs come only from you and the repository.** The coding agent works only issues written by a login in `trusted_dispatchers` that start with the brief marker. `dispatch` reads a brief only from the bot's own briefs directory.
7. **Notices decide nothing.** `tick` and `status` are code. Their output reaches Bot Chat as notices, and the Slack relay also produces only notices. The guard taints any turn that contains one, so a notice cannot make the bot act.
8. **Checks and a human merge.** A unit is ready to merge only when its pull request's checks pass. Only you merge.
9. **An andon cord stops the line.** One for each project and one for the machine. While either is pulled, nothing new starts, and cloud agents at work stop before their next push. Closing the GitHub issue never clears it; only a Hermes command does.

## What you do

- **State work** in a project bot's Bot Chat, and confirm the units it restates. A Slack mention is only a proposal notice; restate the work in Desktop before dispatching.
- **Act on teammate proposals.** When tick notices an `agent:proposal` issue, open the link yourself. If you want it done, tell the project bot the outcome in your own words in Hermes Desktop. The issue text is not a brief and cannot launch work by itself.
- **Act on requests in pull request comments.** A collaborator can ask for the bot on a pull request by mentioning `@<bot name>` (or the `pr_comment_trigger` in `.agents/dispatch.json`). Tick polls new pull request comments, with no webhook, and sends one line per mention: the pull request, the author, and a link, never the comment's text. Treat it like a teammate proposal: open the link, and restate the work in Desktop if you want it done. The first run only starts the clock, so older comments never arrive at once.
- **Confirm chief handoffs** in the target project bot's Bot Chat before that bot briefs or dispatches them (only while `ask_owner` is true).
- **Answer gates.** You get one line saying why. Read the report on the issue, put your answer in the issue body under Context, and ask the bot to launch it again, or tell it to change the plan.
- **Merge** pull requests you get a "ready to merge" line for. Each carries a risk label and its reasons, scored by rules in code from what the pull request changed: `risk:high` for sensitive paths (auth, migrations, payments, CI, infrastructure, secrets), repository high-risk overrides, or a large change; `risk:medium` for dependencies, config, removed tests, weaker verification, no checks, files reverted or hot-fixed in the last 90 days, or changed paths left unowned when `CODEOWNERS` exists. Read the high ones yourself, or ask a suggested reviewer; Hermes never approves or requests a review. Add simple path rules as `risk_paths_high` or `risk_paths_medium`, or named rules in `.agents/dispatch.json`:

  ```json
  {
    "risk_overrides": [
      {"level": "medium", "paths": ["apps/mobile/"], "reason": "mobile release surface"}
    ]
  }
  ```
- **Pull the andon cord** when something is going wrong: tell a project bot "stop", or the bootstrap bot "stop everything". The bot runs `team-kit-gh andon raise <reason>` (or `team-kit-admin andon raise <reason>`), which opens an `agent:andon` issue that every agent at work checks before it pushes. You can open such an issue on GitHub yourself, too; the next tick adopts it. Agents that stop post a `blocked` report, so each becomes a gate. When it is fixed, ask the bot to clear the cord and to relaunch the gated units.
- **Give risky units a second verifier.** Set `"second_verifier_backend": "claude"` (or `codex`) in `.agents/dispatch.json`. When the first verifier passes a unit whose risk scores medium or high, Hermes starts a second one, with a fresh key and an adversarial focus: break the change, rather than confirm it. The unit comes to you only when that second signed verdict passes too, and the notice names both verdicts. Low-risk units get one verifier, as before.
- **Let low-risk units merge themselves.** Set `"auto_merge": "low"` in `.agents/dispatch.json`. A unit then merges without you only when every check holds: risk `low`, checks passed, a signed verdict you accept, a signed review of that exact head with no high or medium finding, and no andon cord pulled. It merges with `--match-head-commit`, so a pull request that moved since it was verified is refused. You get one line, "merged automatically"; anything else still comes to you as "ready for you to merge", with the reason if a merge was tried and failed. The project bot's GitHub token needs Contents and Pull requests write for this.
- **Let a GitHub app review instead of Hermes.** Set `"verifier_backend": "none"`, `"review_backend": "none"`, and `"review_app": "claude[bot]"` (any GitHub app's login ending in `[bot]`). Hermes then starts no review or verifier session. A unit whose checks pass waits in `agent:review`, and every tick reads that app's review of the pull request's current head. An approval, with `auto_merge` set to `low` and the change scoring risk `low`, merges it as above. A change request, failed checks, or a merge conflict with the base branch open a gate, so you can relaunch the unit to fix it. An approval when the risk is higher comes to you as "ready for you to merge". A merge GitHub refuses is tried on the next ticks, up to three times. Only an app's own review counts: a person or a cloud session posting with your token cannot write as `claude[bot]`, and an approval of an older commit, a dismissed one, or one followed by a change request does not count. The project's webhook sends `pull_request_review`, so an approval is acted on within seconds. Setup for the Claude GitHub app: [claude-code-action setup](https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md); the review workflow must submit one review on the head commit; this repository's `.github/workflows/claude-review.yml` is an example that pins the pull request, the commit, and the event in its allowed commands.
- **Let Codex's managed Code Review be the review app.** Set `"review_app": "chatgpt-codex-connector[bot]"` in `.agents/dispatch.json`, with `verifier_backend` and `review_backend` `none` as above. Codex never posts an APPROVED review, so Hermes reads what it does post, and a unit passes only when all of these hold on the pull request's exact head: Codex's summary comment (`<!-- codex-pull-request-review-summary -->`) has a Code Review row that is Completed for that commit; no review comment by Codex on that commit carries a P0 or P1 badge; and either Codex reacted with a thumbs-up after that review completed (its "no findings" signal) or it posted a review of that commit with only lesser findings. A P0 or P1 finding opens a gate. A failed review opens a gate that says to comment `@codex review`. A missing, pending, or stale review (a summary for an older commit, a thumbs-up from before the latest completed review) only waits, and a new push starts over. A bare COMMENTED review is never treated as approval. CI, risk scoring, verification, the andon cord, and `auto_merge` `low` apply exactly as for `claude[bot]`; switching apps is a one-line config change, and `claude[bot]` behaves as before. Only the owner can switch it on, once per repository, in their own browser: connect GitHub to Codex and set up Codex cloud for the repository, then in the [Codex code review settings](https://chatgpt.com/codex/settings/code-review) turn on Code review and Automatic reviews. Without Automatic reviews, comment `@codex review` on a pull request to start a review. If the owner's plan or account cannot reach those settings, the unit waits and the owner merges as before. Codex also stops reviewing when its usage limit is reached (it posted that on #40), which looks like a missing review. This kit's authors could not check subscription eligibility or settings access from a cloud session, and found no Codex review on #145 or #148, so confirm Automatic reviews is on before relying on it.
- **Keep vendored plugins current.** In a repository whose `.agents/dispatch.json` sets `upstream_sync: true`, such as the kit itself, the `team-kit-upstream` job checks every Monday at 09:00 whether the upstream repository in `upstream/source.json` has moved past its pinned commit. If it has, and no sync unit is open, it dispatches one unit from a fixed brief: a cloud agent runs `scripts/sync-upstream.mjs --ref <new commit>`, fixes any rule whose count changed, and opens the pull request, which is verified and reviewed like any other. No model decides anything in the check, and nothing listens for a webhook. You get one line when it starts, and merge the pull request as usual.
- **Act on incidents.** Every 10 minutes by default, or on the interval set with `team-kit-admin add-project --incidents-every`, each project bot polls the default branch's checks, the health URLs in `.agents/dispatch.json`, and optional external alert sources, outbound from your machine, so nothing needs a public address or a webhook. A failure opens an `agent:incident` issue and a [triage-incident](../../plugins/trust-ladder/skills/triage-incident/SKILL.md) session that changes nothing and posts a signed diagnosis there. You get a line when it opens, when the diagnosis arrives, and when it passes again. Only a signed, high-confidence diagnosis that names a suspect commit makes that line propose a fix: `revert <full 40-character sha>`, which also says `git revert -m 1` when the suspect is a merge commit. The line names its incident number; reply `ok #12` (with that number) in that project bot's chat to have it brief exactly that fix as a small unit. A bare `ok` works only while one proposal is pending; with more, the bot asks which incident you mean. Medium, low, or no-suspect diagnoses propose nothing, and a diagnosis never launches work by itself. You can always tell the bot a different fix in your own words. Set `incidents.andon_on_red` to stop the line while the branch is red.

  `incidents.sources` can poll Sentry unresolved issues at or above a level, PagerDuty open incidents on one service, Datadog monitors in Alert, and Grafana firing alerts. The config stores only environment variable names; put the actual tokens in the project bot profile's `.env`. Each `token_env` (and Datadog's `app_key_env`) must start with `TEAM_KIT_ALERT_`, so a config change can never name the bot's GitHub token. A token goes only to a pinned host: `sentry.io`, `api.pagerduty.com`, or Datadog's own API hosts. Grafana, self-hosted Sentry, and any other host must be listed under `alert_hosts` in the machine's `guard.yaml`, which only you edit. Sentry's `min_level` defaults to `error`.

  A source that cannot be read (an expired token, an HTTP error, an unset variable) tells you once and leaves its other checks running. Alert text from these sources never reaches the triage prompt: the prompt gets only the source and a count, and example titles sit in the incident issue under a block marked as untrusted data.

  ```json
  {
    "incidents": {
      "sources": [
        {
          "type": "sentry",
          "name": "sentry-prod",
          "organization": "acme",
          "project": "api",
          "environment": "production",
          "min_level": "error",
          "token_env": "TEAM_KIT_ALERT_SENTRY"
        },
        {
          "type": "pagerduty",
          "name": "pager-api",
          "service_id": "P12345",
          "token_env": "TEAM_KIT_ALERT_PAGERDUTY"
        },
        {
          "type": "datadog",
          "name": "dd-api",
          "site": "datadoghq.com",
          "monitor_tags": ["service:api"],
          "token_env": "TEAM_KIT_ALERT_DATADOG_API",
          "app_key_env": "TEAM_KIT_ALERT_DATADOG_APP"
        },
        {
          "type": "grafana",
          "name": "grafana-prod",
          "url": "https://grafana.example.com",
          "token_env": "TEAM_KIT_ALERT_GRAFANA"
        }
      ]
    }
  }
  ```
- **Act on security findings.** Each morning a cloud session reviews what merged on the default branch since the day before, with the [review-main-security](../../plugins/trust-ladder/skills/review-main-security/SKILL.md) skill, and posts a signed result on the project's `agent:security` issue. You get a line only for high or medium findings. To fix one, tell the project bot what to fix in your own words. Set `security_backend` to `none` in `.agents/dispatch.json` to turn the reviews off. A project created before these jobs existed needs `team-kit-admin activate --bot <bot>` once more to resume them.
- **Close abandoned units as not planned.** If you drop a unit, close its issue with "Close as not planned", not "Close as completed": units that depend on it then gate instead of starting on code that never merged.

Each project bot's `team-kit-status` job sends a weekly summary every Monday at 9:00 in the profile's timezone (Asia/Saigon by default), in three lines grouped by repository: what is in flight, what waits on you, and what is done since the last digest. Ask the bot "what's going on" for the same lines at any time.

## Troubleshooting

- **Every tool call is blocked.** `~/.hermes/team-kit/guard.yaml` or `bots.yaml` is missing, does not parse, or `guard.yaml` is not `version: 2`. The block message names the problem. A version 1 file needs migrating: [hermes/README.md](../../hermes/README.md#migrate-from-version-1).
- **"profile X is not a team-kit bot in bots.yaml".** Bots are created only by `team-kit-admin`. Ask the bootstrap bot, or rerun `install.sh --bootstrap-bot X` for the bootstrap bot.
- **"kit commit ... differs from kit_ref ..." or "... has no vendored kit".** The machine, `.agents/dispatch.json`, and the vendored `.agents/team-kit/KIT.json` must pin the same kit commit. Ask the bootstrap bot to run `team-kit-admin vendor` for the project and merge its pull request, which moves both, then ask the project bot to run `team-kit-gh kit-check`.
- **A cloud agent says the kit's skills did not load.** The repository's vendored kit is missing or its links are broken. Run `python3 .agents/team-kit/vendor_kit.py --check .` in a clone, then vendor again.
- **Nothing launches, and `status` starts with "andon pulled".** A cord is pulled. Ask the bot that pulled it to clear it once the cause is fixed. Closing the `agent:andon` issue on GitHub does not clear it.
- **A unit stays queued.** Its dependencies have not merged, a cross-project dependency's unit-state file is missing or stale, or a limit is full. `team-kit-gh status` lists it; the next tick launches it when it can. Raise `max_in_flight` in a pull request, or `max_in_flight_total` in `guard.yaml`, if you can take more.
- **A unit stays running.** No report yet. Open the session on the issue's launch comment. After a day without a report, tick opens a gate.
- **"done without a pull request".** A Codex task that could not push leaves the pull request to you. Open it from the task, with `Closes #<n>` in its body.
- **Desktop keeps asking you to approve tool calls.** That turn read untrusted text. Approve only a call you asked for. Otherwise deny it and ask again in a new turn.
- **Approvals never appear.** `approvals.mode` is `off`, or Hermes runs with `--yolo`. Set it to `manual` or `smart`. With `off`, Hermes approves the guard's requests by itself.
- **No scheduled work runs.** A new project's jobs stay paused until `team-kit-admin activate`. Check with `hermes -p <project bot> cron list --all`, and manage the shared gateway with `hermes gateway status` from the default profile.
- **Slack mentions do not arrive.** Run `team-kit-slack check`, confirm `slack.enabled`, the bot profile's `.env` token variable names, and the channel id. Then check the user service logs. Do not add a Slack webhook or public route.

## Where this differs from the design

- **Polling, not webhooks.** Updates arrive on the next tick, up to the project's configured interval later, instead of at once. In exchange the machine needs no public address, tunnel, or webhook secret, and a missed event can never strand a unit: every tick reads the current state.
- **Gates, digests, and Slack proposals go to Bot Chat.** Hermes delivers scheduled job output to `bot-chat:<profile>` and cannot target another named session. The Slack relay uses the same Bot Chat path. That delivery arrives as a turn the bot answers, so every delivered line starts with `team-kit notice:`, and the guard taints any turn that contains it.
