# Dispatch work with Hermes

You tell a Hermes bot what you want in Hermes Desktop. It splits the work into units across the project's repositories, writes a brief for each, and starts a Claude Code or Codex cloud agent on it. Every unit ends as one pull request with passing checks. You answer the questions agents cannot, and you merge. Nothing else needs you.

This is the "cloud sessions, one pull request each" level of [Run many agents](run-many-agents.md), driven from chat. How many units run at once per repository comes from its `/trust-audit` score. A program that needs a coordinator running dozens of agents for days is the Orchestrate playbook's job, in Claude Code.

The Hermes side is the kit's [`hermes/`](../../hermes/) module. Its [README](../../hermes/README.md) lists every file it installs.

## The bots

One Hermes machine runs one bootstrap bot and one bot per project. Bot names are free: `ong-xay` and `meo-beo` work as well as `bootstrap` and `shop`.

| Bot | How many | Does | Its only command |
|---|---|---|---|
| Bootstrap bot | One per machine | Sets up a project: its project bot, its repositories' labels, a cloud session per repository that opens the setup pull request, branch protection. Loads the [bootstrap](../../hermes/skills/bootstrap/SKILL.md) skill. You approve every command it runs but `status` | `team-kit-admin` |
| Project bot | One per project | Plans units across the project's repositories, writes a brief per unit, dispatches it. Loads the [dispatch](../../hermes/skills/dispatch/SKILL.md) skill | `team-kit-gh`, for its own repositories only |
| Coding agent | One per unit, in the cloud | Works the brief in faker-mode with the [follow-brief](../../plugins/trust-ladder/skills/follow-brief/SKILL.md) skill, opens one pull request, posts a report | — |

A project is one product, which may span several repositories, such as `acme/shop-api`, `acme/shop-web`, and `acme/shop-mobile`. Its project bot sees only those repositories, so brainstorming one project never pulls in another's context. To brainstorm across projects, add several project bots to a group chat. There they only talk; each bot does work only when you ask it in its own Bot Chat.

The bot that maintains the kit itself is not part of this module. Keep using whatever you use for it.

`~/.hermes/team-kit/bots.yaml` records each bot's role, project, and repositories. Only `team-kit-admin` writes it, and the guard reads it on every tool call.

## One unit

1. You state the work in the project bot's Bot Chat. It reads `PROJECT.md` in the project's home repository, restates the units, and asks you to confirm.
2. Per unit, it writes the brief and runs `team-kit-gh dispatch`. That opens the issue, labeled `agent-task`, then launches the cloud agent and labels the issue `agent:running`. A unit whose dependencies have not merged, or that has no free slot, stays queued.
3. The coding agent checks the issue came from a trusted login with the brief marker, does the work, opens a pull request that closes the issue, and comments a report.
4. Every 30 minutes the bot's `team-kit-tick` job runs `team-kit-gh tick`, which is code, not a model:
   - a `done` report whose pull request passed its checks moves the unit to `agent:review`, and you get a "ready to merge" line;
   - a `partial` or `blocked` report, failed checks, no pull request, or no report within a day opens a gate (`agent:needs-human`), and you get a line saying why;
   - a done report whose checks are still running waits for them;
   - either way the slot frees, and queued units that can start now are launched.
5. You review and merge. The merge closes the issue, which releases the units that depend on it.

The issue's labels, and whether it is closed as completed, are the unit's whole state, so GitHub's issue list filtered by label is your board.

Each repository picks its coding agent once, in `.agents/dispatch.json` (`backend` is `claude` or `codex`). A launch runs one of these. `N` is 1, 2, or 3 for a small, medium, or large unit.

```bash
claude --cloud '/faker-mode Work the brief in OWNER/REPO#123 using the follow-brief skill'
codex cloud exec --env ENV_ID --attempts N '$faker-mode Work the brief in OWNER/REPO#123 using the follow-brief skill'
```

The prompt always starts with `/faker-mode` or `$faker-mode`. The `faker-mode` skill cannot load itself, so the prompt has to run it as a command. For `claude`, the launch first clones the repository into `~/.hermes/team-kit/checkouts/`, because `claude --cloud` works on the repository in its current directory.

### Work across repositories

A change that crosses repositories goes contract first. The project bot plans:

1. a **contract unit** in the home repository, which writes the decision to `docs/decisions/` (and the API's schema, if it keeps one) and changes no behavior,
2. a **provider unit**, usually in the API, which depends on the contract unit,
3. **consumer units** in web and mobile, which depend on the provider unit and run in parallel.

Each brief's Depends on section lists issues as `OWNER/REPO#n`. A unit launches only after each of them is closed by a merged pull request, so every agent starts from code that is already on the default branch.

### How many run at once

Two limits apply to every launch, and both count open issues labeled `agent:running`:

- `max_in_flight` in a repository's `.agents/dispatch.json` caps that repository. The setup session sets it from `/trust-audit`: 1 while any layer is below 2, more once every layer is at 2 or above. Raise it in a pull request as the score improves.
- `max_in_flight_total` in `guard.yaml` caps the whole machine, because every project draws on the same Claude or ChatGPT subscription. Each project bot's GitHub login sees only its own repositories, so each bot records its count in `~/.hermes/team-kit/in-flight/` and reads the others' from there.

A unit over either limit stays queued, and the next tick launches it when a slot frees up, oldest first across the project. A unit frees its slot once its agent has reported and its checks have finished, not when you merge.

## Install

### 1. The machine

Use an always-on machine, such as a home server or a small VM: scheduled jobs run only while the Hermes gateway runs. Hermes Desktop can connect to it from anywhere. Nothing needs a public address.

From a clone of the kit on that machine:

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

`--ref` is a full 40-character kit commit. Every repository's `kit_ref` must equal it. The script fetches the kit at that commit, writes `guard.yaml` once, installs `team-kit-gh` and `team-kit-admin` in `~/.hermes/bin/`, creates the bootstrap bot, refreshes every existing bot from the new kit, and restarts the gateway. Run it again with a new commit to move every bot to it.

Then edit `~/.hermes/team-kit/guard.yaml`:

- `trusted_authors`: your GitHub login, and each project bot's login. Reports by anyone else are ignored, so a copied report cannot move a unit.
- `max_in_flight_total`: how many cloud sessions may run at once on this machine.

The bootstrap bot runs `gh` with its own login. Put a token with Administration and Issues read and write on your repositories in `~/.hermes/profiles/<bootstrap bot>/.env` as `GH_TOKEN`, or sign `gh` in on the machine. Sign in the coding agent too: `claude` for Claude Code cloud, `codex` for Codex cloud.

The installer works with macOS's bundled Bash 3. It needs a Python 3.9+ that can import PyYAML: it tries the Python in Hermes's shebang, then `python3` on `PATH`. Otherwise, install PyYAML in a helper Python and pass it as `HERMES_PYTHON`. Hermes configuration is changed only through `hermes config set` and read back.

### 2. Each project

Open the bootstrap bot in Hermes Desktop Bot Mode and say "set up project shop with acme/shop-api, acme/shop-web, and acme/shop-mobile". It asks for the bot's name, the home repository, and the coding agent, then, with your approval at each step:

1. `team-kit-admin add-project` creates the project bot and its two jobs, paused, and the four labels in each repository.
2. `team-kit-admin setup-repo` starts one cloud session per repository. Each runs faker-mode and the [setup-dispatch](../../plugins/trust-ladder/skills/setup-dispatch/SKILL.md) skill, scores the repository with `/trust-audit`, and opens one pull request: `.agents/dispatch.json`, `.agents/dispatch.md`, any kit setup the repository lacks, and `PROJECT.md` in the home repository. You review and merge each.
3. `team-kit-admin protect` makes the CI check that runs `scripts/check.sh` required on each default branch.
4. You put the project bot's GitHub token in `~/.hermes/profiles/<project bot>/.env` as `GH_TOKEN` yourself: a fine-grained token for the project's repositories with Issues read and write, and Contents, Pull requests, Checks, and Commit statuses read. Never in chat.
5. `team-kit-admin activate` resumes the project bot's jobs.

Then add the project bot in Bot Mode and ask it for one small change, such as a README typo fix, to prove the loop.

## Safety layers

None of these uses a model to decide. Each is code or configuration.

1. **Desktop is the only way in.** No chat platform and no webhook is connected. The machine listens to nothing from outside.
2. **Roles come from one file.** The guard looks up the calling profile in `bots.yaml`. A profile that is not there gets nothing. Each role has a fixed list of tools, whatever its toolsets say. A project bot may run only `team-kit-gh`, write only its own briefs, and read only its briefs and the kit, so it cannot read a token or another bot's files. The bootstrap bot may run only `team-kit-admin`, and every command but `status` waits for your approval.
3. **Projects are walled off.** `team-kit-gh` refuses repositories outside the bot's project, including in a brief's Depends on, and each bot's GitHub token covers only its project.
4. **The guard fails closed.** The `team_kit_guard` plugin checks every tool call against `guard.yaml` and `bots.yaml`. Merges, pushes to `main`, force-pushes, and deletes are denied for every bot. Any error, a timeout, or a missing or invalid file blocks the call.
5. **Untrusted text is marked.** Output from `team-kit-gh read` and web tools arrives wrapped in `<untrusted source=...>` and taints the turn. After that, every tool that changes something asks you to approve it in Desktop.
6. **Briefs come only from you and the repositories.** The coding agent works only issues written by a login in `trusted_dispatchers` that start with the brief marker. `dispatch` reads a brief only from the bot's own briefs directory.
7. **The scheduled jobs decide nothing.** `tick` and `status` are code. Their output reaches Bot Chat as notices, and the guard taints any turn that contains one, so a notice cannot make the bot act.
8. **Checks and a human merge.** A unit is ready to merge only when its pull request's checks pass. Only you merge.

## What you do

- **State work** in a project bot's Bot Chat, and confirm the units it restates.
- **Answer gates.** You get one line saying why. Read the report on the issue, put your answer in the issue body under Context, and ask the bot to launch it again, or tell it to change the plan.
- **Merge** pull requests you get a "ready to merge" line for.
- **Close abandoned units as not planned.** If you drop a unit, close its issue with "Close as not planned", not "Close as completed": units that depend on it then gate instead of starting on code that never merged.

Each project bot's `team-kit-status` job sends three lines at 9:00, 13:00, and 17:00 in the profile's timezone (Asia/Saigon by default), grouped by repository: what is in flight, what waits on you, and what is done since the last digest. Ask the bot "what's going on" for the same lines at any time.

## Troubleshooting

- **Every tool call is blocked.** `~/.hermes/team-kit/guard.yaml` or `bots.yaml` is missing, does not parse, or `guard.yaml` is not `version: 2`. The block message names the problem. A version 1 file needs migrating: [hermes/README.md](../../hermes/README.md#migrate-from-version-1).
- **"profile X is not a team-kit bot in bots.yaml".** Bots are created only by `team-kit-admin`. Ask the bootstrap bot, or rerun `install.sh --bootstrap-bot X` for the bootstrap bot.
- **"kit commit ... differs from kit_ref ...".** The machine and `.agents/dispatch.json` pin different kit commits. Rerun `install.sh --ref` or change `kit_ref` in a pull request, then run `team-kit-gh kit-check --repo OWNER/REPO`.
- **A unit stays queued.** Its dependencies have not merged, or a limit is full. `team-kit-gh status` lists it; the next tick launches it when it can. Raise `max_in_flight` in a pull request, or `max_in_flight_total` in `guard.yaml`, if you can take more.
- **A unit stays running.** No report yet. Open the session on the issue's launch comment. After a day without a report, tick opens a gate.
- **"done without a pull request".** A Codex task that could not push leaves the pull request to you. Open it from the task, with `Closes #<n>` in its body.
- **Desktop keeps asking you to approve tool calls.** That turn read untrusted text. Approve only a call you asked for. Otherwise deny it and ask again in a new turn.
- **Approvals never appear.** `approvals.mode` is `off`, or Hermes runs with `--yolo`. Set it to `manual` or `smart`. With `off`, Hermes approves the guard's requests by itself.
- **No scheduled work runs.** A new project's jobs stay paused until `team-kit-admin activate`. Check with `hermes -p <project bot> cron list --all`, and manage the shared gateway with `hermes gateway status` from the default profile.

## Where this differs from the design

- **Polling, not webhooks.** Updates arrive on the next tick, up to 30 minutes later, instead of at once. In exchange the machine needs no public address, tunnel, or webhook secret, and a missed event can never strand a unit: every tick reads the current state.
- **Gates and digests go to Bot Chat.** Hermes delivers scheduled job output to `bot-chat:<profile>` and cannot target another named session. That delivery arrives as a turn the bot answers, so every delivered line starts with `team-kit notice:`, and the guard taints any turn that contains it.
