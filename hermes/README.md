# team-kit for Hermes

This module runs team-kit dispatch on a [Hermes Agent](https://github.com/NousResearch/hermes-agent) machine: one bootstrap bot that sets projects up, one project bot per project that briefs cloud coding agents (Claude Code or Codex), one pull request per unit, until the owner merges, and an optional chief of staff that routes the owner's intent across projects. The chief reads by default and has one approved write: a local proposal for a project bot. How to use it is in [Dispatch work with Hermes](../docs/how-to/dispatch-with-hermes.md).

It lives outside `plugins/` on purpose. Nothing here loads in a coding agent: plugins are what coding agents load, and this is what the Hermes bots load. The coding agents keep using the `trust-ladder` skills `setup-dispatch` and `follow-brief`, and the brief templates in `plugins/trust-ladder/templates/dispatch/`.

It does one level of the kit's [Run many agents](../docs/how-to/run-many-agents.md) guide: cloud sessions, one pull request each. Programs that need a coordinator running dozens of agents for days are the job of the Orchestrate playbook, or of trust-ladder's `/orchestrate` across cloud sessions.

## How it keeps track

GitHub holds every unit's state, as labels on its issue: `agent-task` (queued), `agent:running`, `agent:verifying` (done and checks passed; a separate verifier session is checking the pull request), `agent:review` (verified, the owner merges), and `agent:needs-human` (a gate). A closed issue is done. Each tick also publishes that project's unit states to `~/.hermes/team-kit/units/<bot>.json`, so another local project can wait on `OWNER/REPO#n` without reading that repository or token. An open issue labelled `agent:proposal` is only a teammate proposal: tick gives the owner its number, author, and link once (never its title), and only for a collaborator's proposal, and no brief is written until the owner restates the work in Hermes Desktop. An open issue labelled `agent:andon` is the andon cord: while it, or the machine's cord, is pulled, nothing new starts and cloud agents at work stop at their next push. Only `team-kit-gh andon clear` (a project) or `team-kit-admin andon clear` (the machine) clears it; closing the issue on GitHub does not. A signed review session can comment findings on each unit pull request head, one open issue labelled `agent:security` collects the security reviews of each push of the default branch, and an issue labelled `agent:incident` tracks each incident the `team-kit-incidents` job finds by polling the default branch's checks, the project's health URLs, and optional Sentry, PagerDuty, Datadog, or Grafana alert sources. A signed high-confidence incident diagnosis that names a suspect makes the notice propose a deterministic small unit, `revert <full sha>` (naming `git revert -m 1` for a merge commit), but only the owner replying `ok #<incident>` makes the project bot brief it. There is no Kanban board. Each project bot's `team-kit-tick` job runs `team-kit-gh tick` every 30 minutes by default, or the interval in `bots.yaml`, which reads the labels and reports, launches what can start, and opens gates, all in code. A machine with `webhooks` in its machine config also runs it as soon as GitHub reports a change (Event-driven launches, below); nothing else is public. The optional Slack relay is outbound-only Socket Mode; it turns mentions into untrusted `team-kit notice:` proposals in Bot Chat and never dispatches work itself.

## Install

On a machine that only runs Hermes, describe it once in a machine config and let one script build it:

```bash
gh repo clone <you>/<kit repository> && cd <kit repository>
cp hermes/templates/machine.yaml .agents/hermes-machine.yaml   # fill it in, commit it
hermes/setup-machine.sh
```

`setup-machine.sh` installs what is missing (gh, Claude Code, Hermes, cloudflared, PyYAML), asks you to sign in where needed, asks for each token with hidden input, installs the kit (`install.sh`), sets up the bots, guard settings, and signed GitHub webhooks (`team-kit-admin machine`), runs the gateway, the Cloudflare Tunnel, and the Slack relay as services, and checks the result end to end. Run it again after any change; it changes only what differs. Run `hermes/setup-machine.sh --help` for its flags.

To set up a machine by hand instead:

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

Run `bash hermes/install.sh --help` for every flag. Then set `trusted_authors` and `max_in_flight_total` in `~/.hermes/team-kit/guard.yaml`, and ask the bootstrap bot for your first project.

### Event-driven launches

With `webhooks` in the machine config, GitHub calls the machine when an issue, comment, pull request, pull request review, check suite, or push changes, and Hermes runs the bot's `team-kit-tick` (or `team-kit-security` for a push) at once. `team-kit-tick` still runs on its schedule, as the fallback for a missed delivery. The pieces:

- Hermes's webhook listener on `127.0.0.1:8644`, with two routes per project bot, `<bot>-tick` and `<bot>-security`, under `platforms.webhook.extra.routes` in `~/.hermes/config.yaml`. Each route fires the named no-agent job, so no model chooses anything.
- One secret per bot, `TEAM_KIT_WEBHOOK_SECRET_<BOT>` in `~/.hermes/.env` (mode 600). Routes name it as `${TEAM_KIT_WEBHOOK_SECRET_<BOT>}`, and GitHub gets it on stdin, so it never reaches `config.yaml`, argv, or a log. Hermes refuses any request whose `X-Hub-Signature-256` does not match.
- A Cloudflare Tunnel (`~/.cloudflared/<tunnel>.yml`) that publishes only `/p/<bot>/webhooks/<route>` on the configured hostname and answers 404 for everything else.
- One repository webhook per route, created or updated in place by `team-kit-admin machine`.

## How cloud agents get the kit

A cloud session starts from a clone of the project's repository and nothing else: it installs no plugins and cannot fetch the kit while the kit's repository is private. So `team-kit-admin vendor` copies the kit, at this machine's commit, into the repository's `.agents/team-kit/` in a pull request, and links its skills into `.claude/skills/` and `.agents/skills/`. It builds the copy with `vendor_kit.py` from the trust-ladder `setup-cloud-agents` skill, and `python3 .agents/team-kit/vendor_kit.py --check .` in the repository verifies it.

To move to a new kit commit, rerun `install.sh --ref <commit>`, then ask the bootstrap bot to run `team-kit-admin vendor` for each project, and merge each pull request. Until then that project's bot refuses to launch, because the machine and the repository would run different kits.

## Layout

| Path | What it is | Installed to |
|---|---|---|
| `setup-machine.sh` | Builds a whole machine from a machine config: packages, sign-ins, tokens, `install.sh`, bots, webhooks, tunnel, services, and checks | — |
| `install.sh` | Fetches the kit at a commit, writes the wrappers, refreshes every bot | — |
| `bin/team_kit_admin.py` | `team-kit-admin`: the bootstrap bot's only command, and the installer's helper | `~/.hermes/bin/team-kit-admin` (a wrapper) |
| `bin/team_kit_gh.py` | `team-kit-gh`: a project bot's only GitHub access, and what its jobs run | `~/.hermes/bin/team-kit-gh` (a wrapper) |
| `bin/team_kit_chief.py` | `team-kit-chief`: the chief of staff's only command; it reads every project and, with approval, hands one hashed draft to one project bot | `~/.hermes/bin/team-kit-chief` (a wrapper) |
| `bin/team_kit_slack.py` | `team-kit-slack`: the optional outbound-only Slack Socket Mode relay | `~/.hermes/bin/team-kit-slack` (a wrapper) |
| `lib/team_kit_contracts.py` | The brief, size, report, and launch markers, and the cloud command lines | read from the kit checkout |
| `lib/team_kit_cloud.py` | Starts a `claude --cloud` session (created with its task as the first message; a signing key follows on stdin) or a `codex cloud exec` task, and returns its id | read from the kit checkout |
| `lib/team_kit_andon.py` | The andon cords: the local records, and the `agent:andon` issue body | read from the kit checkout |
| `lib/team_kit_slack_relay.py` | Slack Socket Mode, Bot Chat delivery, service unit generation, and optional notice mirroring | read from the kit checkout |
| `lib/team_kit_risk.py` | The risk score of a ready pull request and its suggested reviewers, from rules in code | read from the kit checkout |
| `lib/install_config.py` | Reads Hermes config, writes it only through `hermes config set` | read from the kit checkout |
| `lib/team_kit_machine.py` | The machine config: its checks, the guard settings it writes, the webhook routes, and the tunnel config | read from the kit checkout |
| `plugin/team_kit_guard/` | The Hermes plugin that checks every tool call against `guard.yaml` and `bots.yaml` | each bot's `plugins/` |
| `profiles/bootstrap/`, `profiles/project/`, `profiles/chief/` | `SOUL.md` and the config merged into each bot's profile | each bot's profile |
| `skills/bootstrap/`, `skills/dispatch/`, `skills/chief/` | The bots' skills | linked into each bot's `skills/` |
| `cron/project-jobs.md` | `team-kit-tick`, `team-kit-status`, `team-kit-security`, `team-kit-incidents`, and `team-kit-upstream`, created paused per project bot | each project bot's scheduled jobs |
| `templates/guard.yaml` | The machine's policy, written once | `~/.hermes/team-kit/guard.yaml` |
| `templates/machine.yaml` | A machine config to copy and fill in | `.agents/hermes-machine.yaml` in your kit clone |
| `tests/` | `python -m pytest hermes` | — |

Under `~/.hermes/team-kit/`:

- `kit/`, the kit at the pinned commit. Every bot's files come from here.
- `guard.yaml`, the policy. You edit it.
- `bots.yaml`, each bot's role, and for a project bot its project and its one repository. Only `team-kit-admin` writes it.
- `briefs/<bot>/`, the only place a project bot may write.
- `drafts/<chief bot>/`, the only place the chief may write, and the only place `propose` reads from: plain `.md` files of at most 20,000 bytes, no symlinks.
- `proposals/<bot>/`, chief-of-staff handoffs for a project bot. Proposal records expire after 7 days; `team-kit-gh chief-proposals` and `team-kit-gh tick` delete expired and corrupt records.
- `andon/`, the pulled cords: `_machine.json` and one file per project bot.
- `verify/<bot>/`, one private record per verifier: its commit and the key that signs its verdict.
- `reviews/<bot>/`, one private record per unit pull request review: its pull request, head, and the key that signs its finding summary.
- `incidents/<bot>.json`, each open incident, its triage session's key, and the last green commit.
- `security/<bot>.json`, the last commit a security review covered, and the running review's range and key.
- `etag/<bot>/`, that bot's cached GitHub REST responses and ETags, keyed by endpoint, so idle polling can send conditional requests. Bots never share an entry.
- `slack/`, transient `team-kit-slack` Bot Chat notice files, deleted after delivery.
- `checkouts/<owner>__<repo>/repo`, the kept launch checkout every Claude Code session for that project starts from, which the owner trusts once; never pruned. Beside it, the empty working directories `codex cloud exec` runs in and each launch's redacted log, pruned after 3 days.
- `in-flight/<bot>.json`, each project bot's count of running units, for `max_in_flight_total`.
- `units/<bot>.json`, each project bot's recent unit issue states, every issue another bot wants (its `wants` list names the other-project issues its own queued briefs depend on), for cross-project Depends on refs. A dependency must be an `agent-task` unit; `dispatch` refuses a plain issue and tick gates a unit that names one.
- `status/<bot>.json`, when the bot's last digest ran, for "Done since last".
- `proposal-notices/<bot>.json`, the open proposal issue numbers already noticed to the owner (`seen`) and those whose author cannot write, checked once and then skipped (`ignored`). Entries for closed issues are dropped.

## What only you do

- Merge pull requests, including each project's kit-vendoring pull request.
- Put each bot's GitHub token in its profile's `.env`. No bot ever sees a token in chat.
- If you enable Slack, put the Slack app-level and bot tokens in the configured bot profile's `.env` under the variable names in `guard.yaml`. Token values never go in `guard.yaml`, chat, argv, or service unit files.
- Sign the coding agent in on the machine.
- Approve each `team-kit-admin` command the bootstrap bot runs.
- Confirm a chief proposal in the target project bot's Bot Chat before that bot briefs or dispatches it.

## Still to check in the first pilot

These depend on Hermes and the cloud CLIs, and the tests fake them:

- the bot's own GitHub token. Found in the pilot (2026-10-12): a routed turn's commands get only the profile `.env` names listed in `terminal.env_passthrough`, and never `GH_TOKEN`, so `gh` in bot commands runs on the machine's keyring login. `team-kit-gh` reads `CLAUDE_CONFIG_DIR` from the profile itself (`lib/team_kit_env.py`); moving `gh` onto the bot's token waits on a token that can also read checks and Actions runs,
- that a new profile's scheduled jobs run in the shared gateway without a restart,
- the task line `codex cloud exec` prints, which `team_kit_cloud.py` waits for,
- the `claude --cloud` contract (Claude Code 2.1.296): `claude --cloud <message>` creates a session only on a terminal on stdout, from a directory whose GitHub remote is the repository, and asks once per directory whether to trust it; `--cloud` cannot be combined with `-p` to create one; `claude --cloud <session> -p` reads the message on stdin, needs no terminal, prints `Sent to cloud session.`, and queues it into the session even before the sandbox starts. Confirmed with real launches through `team_kit_cloud.Cloud.start` on 2026-10-13: a keyed session (session_014jbXA6v1qeU6yVahovAURT) got its task with the key held back as its first message, replied only `waiting for the signing key`, then got the key on stdin as its next message and did the task with it; an unkeyed session (session_01AUGqsKpA21fJBNWeyJMX44) did the task it got as its only message. Check it with a real launch after every Claude Code upgrade,
- that a cloud session loads skills through the relative links `vendor_kit.py` writes in `.claude/skills/` and `.agents/skills/` (the launch prompt has the agent report "the team-kit skills did not load" if not),
- that the ids `hermes cron list --all` prints are what `hermes cron resume` takes, and that `every 30m` is a valid schedule,
- the `Schedule:` line `hermes cron list --all` prints: `cron_jobs` in `lib/install_config.py` compares it with `every 10m` to decide whether to run `hermes cron edit <id> --schedule`, so verify its format on real Hermes (a mismatch re-edits the job on every `add-project` or install, and a changed schedule would never be noticed if the line is absent),
- that `team-kit-slack`'s Bot Chat delivery is the Bot Chat Desktop shows and the scheduled jobs' `bot-chat:<profile>` delivery uses. It runs `hermes -p <profile> chat --in ~ -c "Bot Chat" --create-if-missing -Q --query-file <file>`, the command Hermes's cron scheduler documents for that delivery (hermes-agent pull request #91487). Hermes offers no separate "deliver to bot-chat" command, and the tests only fake it, so this path is unconfirmed until a real Slack mention shows up in Desktop's Bot Chat,
- the argument names of Hermes's `patch`, `read_file`, and `search_files` tools, which the guard checks (`path`, `mode`, `patch`), and the tool names in each role's allowlist (`ROLE_TOOLS` in `plugin/team_kit_guard/policy.py`).

## Troubleshooting

- **"Claude Code asks whether to trust <path>".** The project's launch checkout is new, or its trust was reset. Run `cd <path> && claude`, choose yes, then `/exit`, and launch again.
- **A Claude launch exits with "Not logged in" or "requires an interactive terminal".** The gateway runs `claude` without your shell. Check that the project bot's `.env` has `CLAUDE_CONFIG_DIR` pointing at the directory your `claude` login uses (rerun `team-kit-admin add-project` from a shell where it is exported), and that nothing replaced `Cloud.popen` or wrapped the launch.
- **"<session> started but did not get its signing key, so it waits idle".** Creating a keyed session (verifier, review, security, or triage) worked, sending its key on stdin did not. The session was told to wait for the key, so it does nothing; delete it at claude.ai/code. The next tick starts a new one.
- **A Codex launch exits before starting a task.** Read `~/.hermes/team-kit/checkouts/<owner>__<repo>/<unit>.log`. It holds the terminal output with escape sequences removed and prompts and signing keys redacted. Run `codex cloud exec` by hand to see whether it needs a sign-in.
- **"printed no session in 180s".** `codex cloud exec` is waiting for input or a login. The same log shows what it printed; the process group is stopped.

## Run bots on your Claude subscription

`team-kit-admin add-project`, `add-chief`, and `add-bootstrap` (or `install.sh --bootstrap-bot NAME`) take `--claude-subscription`. It installs Hermes's [Claude Subscription DirectSDK](https://hermes-agent.nousresearch.com/docs/plugins/claude-subscription-directsdk) provider plugin into that bot's profile only, enables it without permission to replace built-in tools, and sets the bot's `model.provider` to `claude-subscription-directsdk-experimental` and `model.default` to `--model` or `sonnet`. Each turn then runs through the Claude Code CLI logged in on this machine and counts against your Claude subscription, with no API key.

It refuses before changing anything unless `claude` is 2.1.284 or later and `claude auth status` shows a login. Run it from your own shell: the Hermes gateway runs under launchd and does not inherit your shell, so a `CLAUDE_CONFIG_DIR` set there would leave the plugin logged out (HTTP 401). The command records that directory, a path and not a secret, in two places: the bot's `.env` as `CLAUDE_CONFIG_DIR`, which the `claude --cloud` launcher reads, and the root `~/.hermes/.env` as `CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR`, which the plugin reads. The plugin needs the root file because one gateway serves every profile and starts the Claude CLI with its own environment; a profile's `.env` never reaches it, so the bot's gateway turns, such as its replies to delivered notices, would run logged out. A later run from a shell without the variable keeps both lines. Restart the gateway and quit and reopen Hermes Desktop afterwards, so both load the root `.env` again. Run the command again without the flag to leave the provider as it is; switch back with `hermes -p <bot> model`.

## Upgrade from routine launches

For a short while Hermes started Claude Code sessions by firing a Claude Code routine. It is back on `claude --cloud`, whose sessions show up with your other sessions. For each project that used a routine:

1. Rerun `team-kit-admin add-project` for it from your own shell, with `CLAUDE_CONFIG_DIR` exported if your `claude` login uses one. It prints the `launch_checkout`; run `cd <launch_checkout> && claude` once, choose yes, then `/exit`.
2. Delete `TEAM_KIT_ROUTINE_TOKEN` from the project bot's `.env` and `routine:` from its entry in `~/.hermes/team-kit/bots.yaml`; nothing reads them any more.
3. Delete the routine at [claude.ai/code/routines](https://claude.ai/code/routines).

## Upgrade from the webhook version

The first version 2 tracked units on a Kanban board and woke a drain job from GitHub webhooks. If you installed it:

1. Pause and delete each project bot's `team-kit-drain` job (`hermes -p <bot> cron list --all`). Rerunning `install.sh` creates `team-kit-tick` paused; resume it with `team-kit-admin activate`.
2. Delete each repository's GitHub webhook to `/p/<bot>/webhooks/team-kit-<project>`, the `team-kit-<project>` routes under `platforms.webhook.extra.routes` in `~/.hermes/config.yaml`, and `~/.hermes/team-kit/webhook-secret`. Close the tunnel if nothing else uses it.
3. Delete `webhook_base_url` from `guard.yaml`, and `HERMES_KANBAN_BOARD` from each project bot's `.env`.
4. A project is now one repository. In `~/.hermes/team-kit/bots.yaml`, give each project bot `repo: OWNER/REPO` and delete `repos` and `home`; the guard refuses the old keys. A project that spanned several repositories becomes one project per repository, or, better, one combined repository. Then run `team-kit-admin add-project` again for each project, to create the new `agent:review` label.

## Migrate from version 1

Version 1 had two fixed profiles, `dispatcher` and `launcher`, and listed repositories in `guard.yaml`. Version 2 has a bootstrap bot and a bot per project, with roles in `bots.yaml`. The guard refuses a version 1 `guard.yaml`, so every tool call is blocked until you migrate.

1. Pause the old jobs: `hermes -p dispatcher cron list --all`, then `hermes -p dispatcher cron pause <id>` for `dispatcher-drain` and `dispatch-status`.
2. Let in-flight units finish, or note their issues. Version 2 counts running units by the `agent:running` label, which version 1 never set.
3. In `~/.hermes/team-kit/guard.yaml`: set `version: 2`, delete `repos` and `profiles`, and add `max_in_flight_total: 3`. Keep `trusted_authors`, `deny`, `untrusted_readers`, and `read_only_tools`. Compare with `templates/guard.yaml`.
4. Run `bash hermes/install.sh --ref <new kit commit> --bootstrap-bot <name>`.
5. Ask the bootstrap bot to set up a project with the old repositories. Run `setup-repo` only for repositories without `.agents/dispatch.json`; for the rest, run `team-kit-admin vendor`, whose pull request vendors the kit and moves `kit_ref` to the new commit. Delete the old `/p/dispatcher/webhooks/team-kit-github` GitHub webhook.
6. Move the dispatcher's `GH_TOKEN` into the new project bot's `.env` if it should keep the same login, then `team-kit-admin activate`.
7. Once nothing uses them, delete the `dispatcher` and `launcher` profiles with `hermes profile delete`, and the `team-kit-github` route from the default profile's `platforms.webhook.extra.routes`.
