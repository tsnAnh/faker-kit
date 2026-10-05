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
| Chief of staff | Optional, one per machine | Reads every project and changes nothing. Tell it what you want across projects; it says which project bot gets which part, in what order, and writes the message you send each bot. Ask it "what's going on" for one line per project. Loads the [chief](../../hermes/skills/chief/SKILL.md) skill | `team-kit-chief`, read-only |
| Coding agent | One per unit, in the cloud | Works the brief in faker-mode with the [follow-brief](../../plugins/trust-ladder/skills/follow-brief/SKILL.md) skill, opens one pull request, gets its checks green, posts a report | — |

A project is one repository. When a product has an API, a web app, and a mobile app, keep them side by side in that one repository, such as `apps/api/`, `apps/web/`, and `apps/mobile/` in `acme/shop`. Then one cloud agent can carry a feature through all of them in one pull request and prove it end to end, which separate repositories cannot do. The kit's playbooks assume one repository too. A project bot sees only its repository, so brainstorming one project never pulls in another's context. To brainstorm across projects, add several project bots to a group chat. There they only talk; each bot does work only when you ask it in its own Bot Chat. To route one intent across projects, ask the chief of staff instead: ask the bootstrap bot for one (`team-kit-admin add-chief`), and give it a GitHub token that can only read the projects' repositories.

The bot that maintains the kit itself is not part of this module. Keep using whatever you use for it.

`~/.hermes/team-kit/bots.yaml` records each bot's role, project, and repository. Only `team-kit-admin` writes it, and the guard reads it on every tool call.

## One unit

1. You state the work in the project bot's Bot Chat. It reads the repository's `AGENTS.md`, restates the units, and asks you to confirm. A feature that touches the API, web, and mobile is usually one unit.
2. Per unit, it writes the brief and runs `team-kit-gh dispatch`. That opens the issue, labeled `agent-task`, then launches the cloud agent and labels the issue `agent:running`. A unit whose dependencies have not merged, or that has no free slot, stays queued.
3. The coding agent checks the issue came from a trusted login with the brief marker, does the work, opens a pull request that closes the issue, and comments a report.
4. Every 30 minutes the bot's `team-kit-tick` job runs `team-kit-gh tick`, which is code, not a model:
   - a `done` report whose pull request passed its checks starts a verifier: a separate cloud session, on `verifier_backend`, that did not write the change. It checks out the pull request with the `verify-unit` skill, proves each Acceptance line on the real app, and comments a verdict. The unit is labeled `agent:verifying` and keeps its slot;
   - the verifier signs its verdict with a one-use key that only your machine and its session hold, so the coding agent cannot post one for it. A signed verdict listed in `accept_verifications` (by default `live-ui-verified` or `unit-test-verified`) moves the unit to `agent:review` once the checks still pass on the same head, and you get a "ready to merge" line. Any other verdict, a pull request that moved during verification, or no signed verdict within a day opens a gate. `team-kit-gh verify --issue N` (ask the bot) checks the current head again without redoing the coding work; `launch` redoes both;
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

### When work splits into several units

A unit is one pull request. The bot splits work only when one pull request would be too big to review, so it goes in steps, or when parts are independent and can run in parallel. Each brief's Depends on section lists the issues it waits for as `#n`. A unit launches only after each of them is closed by a merged pull request, so every agent starts from code that is already on the default branch.

### How the agent proves its work

The agent drives the real app with the repository's verification skill, named as `verification_skill` in `.agents/dispatch.json` and in each brief's Verify section, and puts the evidence in its report. Then a second session checks it: the agent that wrote a change never grades it. Set `verifier_backend` to the other agent from `backend` when you have both, so a second model family looks at every change; `none` turns verification off. A `codex` verifier needs `codex_env`, and its environment must let tasks comment on issues. Without a verification skill both prove the change with tests only, which reaches `unit-test-verified` at most; the setup pull request then lists `/create-verification-skill` for you. A cloud session runs on Linux, so iOS builds cannot run there; the repository's `AGENTS.md` says what a cloud session cannot prove.

### How many run at once

Two limits apply to every launch, and both count open issues labeled `agent:running`:

- `max_in_flight` in the repository's `.agents/dispatch.json` caps the project. The setup session sets it from `/trust-audit`: 1 while any layer is below 2, more once every layer is at 2 or above. Raise it in a pull request as the score improves.
- `max_in_flight_total` in `guard.yaml` caps the whole machine, because every project draws on the same Claude or ChatGPT subscription. Each project bot's GitHub login sees only its own repository, so each bot records its count in `~/.hermes/team-kit/in-flight/` and reads the others' from there.

A unit over either limit stays queued, and the next tick launches it when a slot frees up, oldest first. A unit frees its slot once its agent has reported and its checks have finished, not when you merge.

## Install

### 1. The machine

Use an always-on machine, such as a home server or a small VM: scheduled jobs run only while the Hermes gateway runs. Hermes Desktop can connect to it from anywhere. Nothing needs a public address.

From a clone of the kit on that machine:

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

`--ref` is a full 40-character kit commit. Every repository's `kit_ref` must equal it. The script fetches the kit at that commit, writes `guard.yaml` once, installs `team-kit-gh` and `team-kit-admin` in `~/.hermes/bin/`, creates the bootstrap bot, refreshes every existing bot from the new kit, and restarts the gateway. Run it again with a new commit to move every bot to it, then ask the bootstrap bot to vendor the new commit into each project's repository (below).

Then edit `~/.hermes/team-kit/guard.yaml`:

- `trusted_authors`: your GitHub login, and each project bot's login. Reports by anyone else are ignored, so a copied report cannot move a unit.
- `max_in_flight_total`: how many cloud sessions may run at once on this machine.

The bootstrap bot runs `gh` with its own login. Put a token with Administration, Issues, Contents, and Pull requests read and write on your repositories in `~/.hermes/profiles/<bootstrap bot>/.env` as `GH_TOKEN`, or sign `gh` in on the machine. Sign in the coding agent too: `claude` for Claude Code cloud, `codex` for Codex cloud.

The installer works with macOS's bundled Bash 3. It needs a Python 3.9+ that can import PyYAML: it tries the Python in Hermes's shebang, then `python3` on `PATH`. Otherwise, install PyYAML in a helper Python and pass it as `HERMES_PYTHON`. Hermes configuration is changed only through `hermes config set` and read back.

### 2. Each project

Open the bootstrap bot in Hermes Desktop Bot Mode and say "set up project shop for acme/shop". It asks for the bot's name and the coding agent, then, with your approval at each step:

1. `team-kit-admin add-project` creates the project bot and its three jobs, paused, and the ten labels in the repository.
2. `team-kit-admin vendor` opens a pull request that copies the kit, at the machine's commit, into the repository's `.agents/team-kit/` and links its skills into `.claude/skills/` and `.agents/skills/`. Cloud sessions start from a clone of the repository, and cannot install plugins or fetch a private kit, so this copy is where they get the kit's skills. The repository's own skills keep their place. You review and merge it.
3. `team-kit-admin setup-repo` starts one cloud session. It runs faker-mode and the [setup-dispatch](../../plugins/trust-ladder/skills/setup-dispatch/SKILL.md) skill, scores the repository with `/trust-audit`, and opens one pull request: `.agents/dispatch.json` (with the `verification_skill`), `.agents/dispatch.md`, a Layout section in `AGENTS.md`, and any kit setup the repository lacks. You review and merge it.
4. `team-kit-admin protect` makes the CI check that runs `scripts/check.sh` required on the default branch.
5. You put the project bot's GitHub token in `~/.hermes/profiles/<project bot>/.env` as `GH_TOKEN` yourself: a fine-grained token for the repository with Issues read and write, and Contents, Pull requests, Checks, and Commit statuses read. Never in chat.
6. `team-kit-admin activate` resumes the project bot's jobs.

Then add the project bot in Bot Mode and ask it for one small change, such as a README typo fix, to prove the loop.

## Safety layers

None of these uses a model to decide. Each is code or configuration.

1. **Desktop is the only way in.** No chat platform and no webhook is connected. The machine listens to nothing from outside.
2. **Roles come from one file.** The guard looks up the calling profile in `bots.yaml`. A profile that is not there gets nothing. Each role has a fixed list of tools, whatever its toolsets say. A project bot may run only `team-kit-gh`, write only its own briefs, and read only its briefs and the kit, so it cannot read a token or another bot's files. The bootstrap bot may run only `team-kit-admin`, and every command but `status` waits for your approval.
3. **Projects are walled off.** `team-kit-gh` works only on the bot's own repository, including in a brief's Depends on, and each bot's GitHub token covers only that repository.
4. **The guard fails closed.** The `team_kit_guard` plugin checks every tool call against `guard.yaml` and `bots.yaml`. Merges, pushes to `main`, force-pushes, and deletes are denied for every bot. Any error, a timeout, or a missing or invalid file blocks the call.
5. **Untrusted text is marked.** Output from `team-kit-gh read` and web tools arrives wrapped in `<untrusted source=...>` and taints the turn. After that, every tool that changes something asks you to approve it in Desktop.
6. **Briefs come only from you and the repository.** The coding agent works only issues written by a login in `trusted_dispatchers` that start with the brief marker. `dispatch` reads a brief only from the bot's own briefs directory.
7. **The scheduled jobs decide nothing.** `tick` and `status` are code. Their output reaches Bot Chat as notices, and the guard taints any turn that contains one, so a notice cannot make the bot act.
8. **Checks and a human merge.** A unit is ready to merge only when its pull request's checks pass. Only you merge.
9. **An andon cord stops the line.** One for each project and one for the machine. While either is pulled, nothing new starts, and cloud agents at work stop before their next push. Closing the GitHub issue never clears it; only a Hermes command does.

## What you do

- **State work** in a project bot's Bot Chat, and confirm the units it restates.
- **Answer gates.** You get one line saying why. Read the report on the issue, put your answer in the issue body under Context, and ask the bot to launch it again, or tell it to change the plan.
- **Merge** pull requests you get a "ready to merge" line for. Each carries a risk label and its reasons, scored by rules in code from what the pull request changed: `risk:high` for sensitive paths (auth, migrations, payments, CI, infrastructure, secrets) or a large change, `risk:medium` for dependencies, config, removed tests, weaker verification, or no checks. Read the high ones yourself, or ask a suggested reviewer; Hermes never approves or requests a review. Add your repository's own sensitive paths as `risk_paths_high` or `risk_paths_medium` in `.agents/dispatch.json`.
- **Pull the andon cord** when something is going wrong: tell a project bot "stop", or the bootstrap bot "stop everything". The bot runs `team-kit-gh andon raise <reason>` (or `team-kit-admin andon raise <reason>`), which opens an `agent:andon` issue that every agent at work checks before it pushes. You can open such an issue on GitHub yourself, too; the next tick adopts it. Agents that stop post a `blocked` report, so each becomes a gate. When it is fixed, ask the bot to clear the cord and to relaunch the gated units.
- **Act on security findings.** Each morning a cloud session reviews what merged on the default branch since the day before, with the [review-main-security](../../plugins/trust-ladder/skills/review-main-security/SKILL.md) skill, and posts a signed result on the project's `agent:security` issue. You get a line only for high or medium findings. To fix one, tell the project bot what to fix in your own words. Set `security_backend` to `none` in `.agents/dispatch.json` to turn the reviews off. A project created before this job existed needs `team-kit-admin activate --bot <bot>` once more to resume it.
- **Close abandoned units as not planned.** If you drop a unit, close its issue with "Close as not planned", not "Close as completed": units that depend on it then gate instead of starting on code that never merged.

Each project bot's `team-kit-status` job sends three lines at 9:00, 13:00, and 17:00 in the profile's timezone (Asia/Saigon by default), grouped by repository: what is in flight, what waits on you, and what is done since the last digest. Ask the bot "what's going on" for the same lines at any time.

## Troubleshooting

- **Every tool call is blocked.** `~/.hermes/team-kit/guard.yaml` or `bots.yaml` is missing, does not parse, or `guard.yaml` is not `version: 2`. The block message names the problem. A version 1 file needs migrating: [hermes/README.md](../../hermes/README.md#migrate-from-version-1).
- **"profile X is not a team-kit bot in bots.yaml".** Bots are created only by `team-kit-admin`. Ask the bootstrap bot, or rerun `install.sh --bootstrap-bot X` for the bootstrap bot.
- **"kit commit ... differs from kit_ref ..." or "... has no vendored kit".** The machine, `.agents/dispatch.json`, and the vendored `.agents/team-kit/KIT.json` must pin the same kit commit. Ask the bootstrap bot to run `team-kit-admin vendor` for the project and merge its pull request, which moves both, then ask the project bot to run `team-kit-gh kit-check`.
- **A cloud agent says the team-kit skills did not load.** The repository's vendored kit is missing or its links are broken. Run `python3 .agents/team-kit/vendor_kit.py --check .` in a clone, then vendor again.
- **Nothing launches, and `status` starts with "andon pulled".** A cord is pulled. Ask the bot that pulled it to clear it once the cause is fixed. Closing the `agent:andon` issue on GitHub does not clear it.
- **A unit stays queued.** Its dependencies have not merged, or a limit is full. `team-kit-gh status` lists it; the next tick launches it when it can. Raise `max_in_flight` in a pull request, or `max_in_flight_total` in `guard.yaml`, if you can take more.
- **A unit stays running.** No report yet. Open the session on the issue's launch comment. After a day without a report, tick opens a gate.
- **"done without a pull request".** A Codex task that could not push leaves the pull request to you. Open it from the task, with `Closes #<n>` in its body.
- **Desktop keeps asking you to approve tool calls.** That turn read untrusted text. Approve only a call you asked for. Otherwise deny it and ask again in a new turn.
- **Approvals never appear.** `approvals.mode` is `off`, or Hermes runs with `--yolo`. Set it to `manual` or `smart`. With `off`, Hermes approves the guard's requests by itself.
- **No scheduled work runs.** A new project's jobs stay paused until `team-kit-admin activate`. Check with `hermes -p <project bot> cron list --all`, and manage the shared gateway with `hermes gateway status` from the default profile.

## Where this differs from the design

- **Polling, not webhooks.** Updates arrive on the next tick, up to 30 minutes later, instead of at once. In exchange the machine needs no public address, tunnel, or webhook secret, and a missed event can never strand a unit: every tick reads the current state.
- **Gates and digests go to Bot Chat.** Hermes delivers scheduled job output to `bot-chat:<profile>` and cannot target another named session. That delivery arrives as a turn the bot answers, so every delivered line starts with `team-kit notice:`, and the guard taints any turn that contains it.
