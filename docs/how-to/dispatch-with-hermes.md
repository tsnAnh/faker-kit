# Dispatch work with Hermes

You tell a Hermes bot what you want in Hermes Desktop. It splits the work into units across the project's repositories, opens one GitHub issue per unit, and starts a Claude Code or Codex cloud agent on each. Every unit ends as a pull request with passing checks. You answer the questions agents cannot, and you merge. Nothing else needs you.

The Hermes side is the kit's [`hermes/`](../../hermes/) module. Its [README](../../hermes/README.md) lists every file it installs.

## The bots

One Hermes machine runs one bootstrap bot and one bot per project. Bot names are free: `ong-xay` and `meo-beo` work as well as `bootstrap` and `shop`.

| Bot | How many | Does | Its only command |
|---|---|---|---|
| Bootstrap bot | One per machine | Sets up a project: its project bot, its Kanban board, its repositories' labels and webhooks, a cloud session per repository that opens the setup pull request, branch protection. Loads the [bootstrap](../../hermes/skills/bootstrap/SKILL.md) skill. You approve every command it runs but `status` | `team-kit-admin` |
| Project bot | One per project | Plans units across the project's repositories, writes a brief per unit, opens the issue, launches the cloud agent, moves cards as GitHub events arrive. Loads the [dispatch](../../hermes/skills/dispatch/SKILL.md) skill | `team-kit-gh`, for its own repositories only |
| Coding agent | One per unit, in the cloud | Works the brief in faker-mode with the [follow-brief](../../plugins/trust-ladder/skills/follow-brief/SKILL.md) skill, opens one pull request, posts a report | — |

A project is one product, which may span several repositories, such as `acme/shop-api`, `acme/shop-web`, and `acme/shop-mobile`. Its project bot sees only those repositories and its own Kanban board, so brainstorming one project never pulls in another's context. To brainstorm across projects, add several project bots to a group chat. There they only talk; each bot does work only when you ask it in its own Bot Chat.

The bot that maintains the kit itself is not part of this module. Keep using whatever you use for it.

`~/.hermes/team-kit/bots.yaml` records each bot's role, project, and repositories. Only `team-kit-admin` writes it, and the guard reads it on every tool call.

## One unit

1. You state the work in the project bot's Bot Chat. It reads `PROJECT.md` in the project's home repository, restates the units, and asks you to confirm.
2. Per unit, it creates a blocked card, writes the brief, and runs `team-kit-gh open-issue`. The issue is labeled `agent-task`.
3. It runs `team-kit-gh launch`. That checks the issue, its dependencies, and the in-flight limits, labels the issue `agent:running`, and starts the cloud agent. A unit whose dependencies are not merged yet is queued.
4. The coding agent checks the issue came from a trusted login with the brief marker, does the work, opens a pull request with the PR marker, and comments a report.
5. GitHub sends `pull_request`, `issue_comment`, and `check_suite` events to the gateway. The project's `github-triage.py` drops everything that does not change one of its units and wakes the project bot's `team-kit-drain` job for the rest.
6. When the pull request's required checks pass, the drain completes the card. You review and merge. The merge closes the issue, which frees its slot and lets the drain launch the units that waited for it.

Each repository picks its coding agent once, in `.agents/dispatch.json` (`backend` is `claude` or `codex`). `team-kit-gh launch` runs one of these. `N` is 1, 2, or 3 for a small, medium, or large unit.

```bash
claude --cloud '/faker-mode Work the brief in OWNER/REPO#123 using the follow-brief skill'
codex cloud exec --env ENV_ID --attempts N '$faker-mode Work the brief in OWNER/REPO#123 using the follow-brief skill'
```

The prompt always starts with `/faker-mode` or `$faker-mode`. The `faker-mode` skill cannot load itself, so the prompt has to run it as a command. For `claude`, `launch` first clones the repository into `~/.hermes/team-kit/checkouts/`, because `claude --cloud` works on the repository in its current directory.

### Work across repositories

A change that crosses repositories goes contract first. The project bot plans:

1. a **contract unit** in the home repository, which writes the decision to `docs/decisions/` (and the API's schema, if it keeps one) and changes no behavior,
2. a **provider unit**, usually in the API, which depends on the contract unit,
3. **consumer units** in web and mobile, which depend on the provider unit and run in parallel.

Each brief's Depends on section lists issues as `OWNER/REPO#n`. `team-kit-gh launch` refuses a unit until each of them is closed by a merged pull request, so every agent starts from code that is already on the default branch.

### How many run at once

Two limits apply to every launch. `max_in_flight` in a repository's `.agents/dispatch.json` caps that repository. `max_in_flight_total` in `guard.yaml` caps the whole machine, because every project draws on the same Claude or ChatGPT subscription. Both count open issues labeled `agent:running`. Each project bot's GitHub login sees only its own repositories, so each bot records its own count in `~/.hermes/team-kit/in-flight/` and reads the others' from there; a count older than three hours is ignored. A unit over either limit is queued, and the drain launches it when a slot frees up.

## Install

### 1. The machine

Use an always-on machine, such as a home server or a small VM. Cron, the webhook, and Kanban run only while the gateway runs. Hermes Desktop can connect to it from anywhere.

From a clone of the kit on that machine:

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

`--ref` is a full 40-character kit commit. Every repository's `kit_ref` must equal it. The script fetches the kit at that commit, writes `guard.yaml` and the webhook secret once, installs `team-kit-gh` and `team-kit-admin` in `~/.hermes/bin/`, creates the bootstrap bot, refreshes every existing bot from the new kit, and restarts the gateway. Run it again with a new commit to move every bot to it.

Then edit `~/.hermes/team-kit/guard.yaml`:

- `trusted_authors`: your GitHub login, and each project bot's login. The webhook ignores pull requests and reports by anyone else, so a copied marker cannot move a card.
- `max_in_flight_total`: how many cloud sessions may run at once on this machine.
- `webhook_base_url`: this machine's public HTTPS address (below).

The bootstrap bot runs `gh` with its own login. Put a token with Administration, Webhooks, and Issues read and write on your repositories in `~/.hermes/profiles/<bootstrap bot>/.env` as `GH_TOKEN`, or sign `gh` in on the machine. Sign in the coding agent too: `claude` for Claude Code cloud, `codex` for Codex cloud.

The installer works with macOS's bundled Bash 3. It needs a Python 3.9+ that can import PyYAML: it tries the Python in Hermes's shebang, then `python3` on `PATH`. Otherwise, install PyYAML in a helper Python and pass it as `HERMES_PYTHON`. Hermes configuration is changed only through `hermes config set` and read back.

### 2. The webhook tunnel

GitHub must reach the shared webhook listener over HTTPS (port 8644 on a fresh install). Use a tunnel or a reverse proxy with TLS, such as Cloudflare Tunnel or Tailscale Funnel, and expose only that listener. Each project has its own route, `https://<public host>/p/<project bot>/webhooks/team-kit-<project>`, signed with the secret in `~/.hermes/team-kit/webhook-secret`. `team-kit-admin add-repo` creates the GitHub webhook for you once `webhook_base_url` is set.

Check `http://localhost:8644/health` for listener health. A signed GitHub `ping` to a route returns 200 with `status: ignored`; an unsigned request returns 401.

### 3. Each project

Open the bootstrap bot in Hermes Desktop Bot Mode and say "set up project shop with acme/shop-api, acme/shop-web, and acme/shop-mobile". It asks for the bot's name, the home repository, and the coding agent, then, with your approval at each step:

1. `team-kit-admin add-project` creates the project bot, its board, its webhook route, and its two jobs, paused.
2. `team-kit-admin add-repo` labels each repository and points its webhook here.
3. `team-kit-admin bootstrap-repo` starts one cloud session per repository. Each runs faker-mode and the [setup-dispatch](../../plugins/trust-ladder/skills/setup-dispatch/SKILL.md) skill and opens one pull request: `.agents/dispatch.json`, `.agents/dispatch.md`, any kit setup the repository lacks, and `PROJECT.md` in the home repository. You review and merge each.
4. `team-kit-admin protect` makes the CI check that runs `scripts/check.sh` required on each default branch. A card completes only when required checks pass.
5. You put the project bot's GitHub token in `~/.hermes/profiles/<project bot>/.env` as `GH_TOKEN` yourself: a fine-grained token for the project's repositories with Issues read and write, and Contents, Pull requests, Checks, and Commit statuses read. Never in chat.
6. `team-kit-admin activate` resumes the project bot's jobs.

Then add the project bot in Bot Mode and ask it for one small change, such as a README typo fix, to prove the loop.

## Safety layers

None of these uses a model to decide. Each is code or configuration.

1. **Desktop is the only way in.** No chat platform is connected. Webhooks wake only a project bot's drain job.
2. **Roles come from one file.** The guard looks up the calling profile in `bots.yaml`. A profile that is not there gets nothing. Each role has a fixed list of tools, whatever its toolsets say. A project bot may run only `team-kit-gh`, write only its own briefs, and read only its briefs and the kit, so it cannot read the webhook secret, a token, or another bot's files. The bootstrap bot may run only `team-kit-admin`, and every command but `status` waits for your approval.
3. **Projects are walled off.** `team-kit-gh` refuses repositories outside the bot's project, including in a brief's Depends on. Each project has its own Kanban board, webhook route, and triage script, which names its bot, so one project's events never wake another's bot.
4. **Secrets stay off command lines.** The webhook secret goes to Hermes's config by a file write and to GitHub on standard input, never as an argument. Tokens live only in profile `.env` files you edit yourself.
5. **The guard fails closed.** The `team_kit_guard` plugin checks every tool call against `guard.yaml` and `bots.yaml`. Merges, pushes to `main`, force-pushes, and deletes are denied for every bot. Any error, a timeout, or a missing or invalid file blocks the call.
6. **Untrusted text is marked.** Output from `team-kit-gh read` and web tools arrives wrapped in `<untrusted source=...>` and taints the turn. After that, every tool that changes something asks you to approve it in Desktop. Cron runs deny approvals, so a drain cannot act on text it should not trust.
7. **Briefs come only from you and the repositories.** The coding agent works only issues written by a login in `trusted_dispatchers` that start with the brief marker. `open-issue` reads a brief only from the bot's own briefs directory.
8. **Checks and a human merge.** A card completes only when the repository's required checks pass on the pull request's head. Only you merge.

## What you do

- **State work** in a project bot's Bot Chat, and confirm the units it restates.
- **Answer gates.** When an agent reports `partial` or `blocked`, the issue gets `agent:needs-human`, its slot is freed, and you get one line in Bot Chat. Read the report, put your answer in the issue body under Context, and ask the bot to launch it again, or tell it to change the plan.
- **Merge** pull requests whose cards completed.
- **Close abandoned units.** If you close a unit's pull request without merging, close its issue too, so its `agent:running` label stops counting against the limits.

Each project bot's `team-kit-status` job sends three lines at 9:00, 13:00, and 17:00 in the profile's timezone (Asia/Saigon by default), grouped by repository: what is in flight, what waits on you, and what is done since the last digest.

## Troubleshooting

- **Every tool call is blocked.** `~/.hermes/team-kit/guard.yaml` or `bots.yaml` is missing, does not parse, or `guard.yaml` is not `version: 2`. The block message names the problem. A version 1 file needs migrating: [hermes/README.md](../../hermes/README.md#migrate-from-version-1).
- **"profile X is not a team-kit bot in bots.yaml".** Bots are created only by `team-kit-admin`. Ask the bootstrap bot, or rerun `install.sh --bootstrap-bot X` for the bootstrap bot.
- **"kit commit ... differs from kit_ref ...".** The machine and `.agents/dispatch.json` pin different kit commits. Rerun `install.sh --ref` or change `kit_ref` in a pull request, then run `team-kit-gh kit-check --repo OWNER/REPO`.
- **"waiting for dependencies".** Normal. The unit launches when the issues it depends on are closed by merged pull requests.
- **"repo limit" or "machine limit".** Normal. The drain launches the unit when a slot frees up. Raise `max_in_flight` in a pull request, or `max_in_flight_total` in `guard.yaml`, if you can take more.
- **"already launched (agent:running)".** The unit's agent is running. A gate removes the label, so a unit that reported is never stuck here. If a session died without reporting, remove the label by hand and ask the bot to launch it again.
- **A card is stuck "waiting for PR".** No pull request with `<!-- team-kit:pr v1 card=<card> issue=<n> -->` in its body exists yet. Open the session on the card's `launched:` comment. A Codex task that could not push leaves the pull request to you; open it from the task and keep the marker. If the pull request exists, check the webhook's Recent Deliveries in GitHub, or run `team-kit-admin status` in the bootstrap bot.
- **A card does not complete though checks are green.** The repository has no required checks, or the project bot's `GH_TOKEN` cannot read them. Kanban reads checks as the card's assignee, the project bot.
- **Desktop keeps asking you to approve tool calls.** That turn read untrusted text. Approve only a call you asked for. Otherwise deny it and ask again in a new turn.
- **Approvals never appear.** `approvals.mode` is `off`, or Hermes runs with `--yolo`. Set it to `manual` or `smart`. With `off`, Hermes approves the guard's requests by itself.
- **No scheduled work runs.** A new project's jobs stay paused until `team-kit-admin activate`. Check with `hermes -p <project bot> cron list --all`, and manage the shared gateway with `hermes gateway status` from the default profile.

## Where this differs from the design

- **Cards never leave blocked until they complete.** Kanban would otherwise spawn the card's assignee. Dependencies live in briefs and GitHub, where `team-kit-gh launch` enforces them, not in Kanban parent links.
- **The webhook route uses `cron_job` without `coalesce`.** Hermes does not allow both. The drain re-reads every open unit each run, so bursts of events are harmless.
- **Gates and digests go to Bot Chat.** Hermes cron delivers to `bot-chat:<profile>` and cannot target another named session. Bot Chat delivery arrives as a turn the bot answers, so every delivered line starts with `team-kit notice:`, and the guard taints any turn that contains it.
- **Events during a drain are dropped, not queued.** Hermes refuses to start a cron job that is already running. The drain sweeps every open unit, and its two-hour schedule catches what a dropped event missed.
- **Cards use Kanban's default retry limit.** `kanban_create` takes no per-card retry count. Set `kanban.failure_limit` in a project bot's config to change it.
