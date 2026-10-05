# team-kit for Hermes

This module runs team-kit dispatch on a [Hermes Agent](https://github.com/NousResearch/hermes-agent) machine: one bootstrap bot that sets projects up, one project bot per project that briefs cloud coding agents (Claude Code or Codex), one pull request per unit, until the owner merges, and an optional chief of staff that routes the owner's intent across projects. The chief reads by default and has one approved write: a local proposal for a project bot. How to use it is in [Dispatch work with Hermes](../docs/how-to/dispatch-with-hermes.md).

It lives outside `plugins/` on purpose. Nothing here loads in a coding agent: plugins are what coding agents load, and this is what the Hermes bots load. The coding agents keep using the `trust-ladder` skills `setup-dispatch` and `follow-brief`, and the brief templates in `plugins/trust-ladder/templates/dispatch/`.

It does one level of the kit's [Run many agents](../docs/how-to/run-many-agents.md) guide: cloud sessions, one pull request each. Programs that need a coordinator running dozens of agents for days are the job of the Orchestrate playbook, or of trust-ladder's `/orchestrate` across cloud sessions.

## How it keeps track

GitHub holds every unit's state, as labels on its issue: `agent-task` (queued), `agent:running`, `agent:verifying` (done and checks passed; a separate verifier session is checking the pull request), `agent:review` (verified, the owner merges), and `agent:needs-human` (a gate). A closed issue is done. An open issue labelled `agent:andon` is the andon cord: while it, or the machine's cord, is pulled, nothing new starts and cloud agents at work stop at their next push. Only `team-kit-gh andon clear` (a project) or `team-kit-admin andon clear` (the machine) clears it; closing the issue on GitHub does not. One open issue labelled `agent:security` collects the daily security reviews of the default branch, and an issue labelled `agent:incident` tracks each incident the `team-kit-incidents` job finds by polling the default branch's checks and the project's health URLs. There is no Kanban board, no webhook, and no public address: each project bot's `team-kit-tick` job runs `team-kit-gh tick` every 30 minutes, which reads the labels and reports, launches what can start, and opens gates, all in code.

## Install

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

Run `bash hermes/install.sh --help` for every flag. Then set `trusted_authors` and `max_in_flight_total` in `~/.hermes/team-kit/guard.yaml`, and ask the bootstrap bot for your first project.

## How cloud agents get the kit

A cloud session starts from a clone of the project's repository and nothing else: it installs no plugins and cannot fetch the kit while the kit's repository is private. So `team-kit-admin vendor` copies the kit, at this machine's commit, into the repository's `.agents/team-kit/` in a pull request, and links its skills into `.claude/skills/` and `.agents/skills/`. It builds the copy with `vendor_kit.py` from the trust-ladder `setup-cloud-agents` skill, and `python3 .agents/team-kit/vendor_kit.py --check .` in the repository verifies it.

To move to a new kit commit, rerun `install.sh --ref <commit>`, then ask the bootstrap bot to run `team-kit-admin vendor` for each project, and merge each pull request. Until then that project's bot refuses to launch, because the machine and the repository would run different kits.

## Layout

| Path | What it is | Installed to |
|---|---|---|
| `install.sh` | Fetches the kit at a commit, writes the wrappers, refreshes every bot | — |
| `bin/team_kit_admin.py` | `team-kit-admin`: the bootstrap bot's only command, and the installer's helper | `~/.hermes/bin/team-kit-admin` (a wrapper) |
| `bin/team_kit_gh.py` | `team-kit-gh`: a project bot's only GitHub access, and what its jobs run | `~/.hermes/bin/team-kit-gh` (a wrapper) |
| `bin/team_kit_chief.py` | `team-kit-chief`: the chief of staff's only command; it reads every project and, with approval, writes a proposal for one project bot | `~/.hermes/bin/team-kit-chief` (a wrapper) |
| `lib/team_kit_contracts.py` | The brief, size, report, and launch markers, and the cloud command lines | read from the kit checkout |
| `lib/team_kit_cloud.py` | Starts a `claude --cloud` or `codex cloud exec` session and waits for its id | read from the kit checkout |
| `lib/team_kit_andon.py` | The andon cords: the local records, and the `agent:andon` issue body | read from the kit checkout |
| `lib/team_kit_risk.py` | The risk score of a ready pull request and its suggested reviewers, from rules in code | read from the kit checkout |
| `lib/install_config.py` | Reads Hermes config, writes it only through `hermes config set` | read from the kit checkout |
| `plugin/team_kit_guard/` | The Hermes plugin that checks every tool call against `guard.yaml` and `bots.yaml` | each bot's `plugins/` |
| `profiles/bootstrap/`, `profiles/project/`, `profiles/chief/` | `SOUL.md` and the config merged into each bot's profile | each bot's profile |
| `skills/bootstrap/`, `skills/dispatch/`, `skills/chief/` | The bots' skills | linked into each bot's `skills/` |
| `cron/project-jobs.md` | `team-kit-tick`, `team-kit-status`, `team-kit-security`, and `team-kit-incidents`, created paused per project bot | each project bot's scheduled jobs |
| `templates/guard.yaml` | The machine's policy, written once | `~/.hermes/team-kit/guard.yaml` |
| `tests/` | `python -m pytest hermes` | — |

Under `~/.hermes/team-kit/`:

- `kit/`, the kit at the pinned commit. Every bot's files come from here.
- `guard.yaml`, the policy. You edit it.
- `bots.yaml`, each bot's role, and for a project bot its project and its one repository. Only `team-kit-admin` writes it.
- `briefs/<bot>/`, the only place a project bot may write.
- `proposals/<bot>/`, chief-of-staff handoffs for a project bot. Proposal records expire after 7 days; `team-kit-gh proposals` and `team-kit-gh tick` ignore or prune expired records.
- `andon/`, the pulled cords: `_machine.json` and one file per project bot.
- `verify/<bot>/`, one private record per verifier: its commit and the key that signs its verdict.
- `incidents/<bot>.json`, each open incident, its triage session's key, and the last green commit.
- `security/<bot>.json`, the last commit a security review covered, and the running review's range and key.
- `checkouts/`, shallow clones `claude --cloud` starts from. Pruned after 3 days.
- `in-flight/<bot>.json`, each project bot's count of running units, for `max_in_flight_total`.
- `status/<bot>.json`, when the bot's last digest ran, for "Done since last".

## What only you do

- Merge pull requests, including each project's kit-vendoring pull request.
- Put each bot's GitHub token in its profile's `.env`. No bot ever sees a token in chat.
- Sign the coding agent in on the machine.
- Approve each `team-kit-admin` command the bootstrap bot runs.
- Confirm a chief proposal in the target project bot's Bot Chat before that bot briefs or dispatches it.

## Still to check in the first pilot

These depend on Hermes and the cloud CLIs, and the tests fake them:

- that a profile's `.env` (`GH_TOKEN`) reaches its terminal and its scheduled jobs,
- that a new profile's scheduled jobs run in the shared gateway without a restart,
- the session line `claude --cloud` and `codex cloud exec` print, which `team_kit_cloud.py` waits for,
- that a cloud session loads skills through the relative links `vendor_kit.py` writes in `.claude/skills/` and `.agents/skills/` (the launch prompt has the agent report "the team-kit skills did not load" if not),
- that the ids `hermes cron list --all` prints are what `hermes cron resume` takes, and that `every 30m` is a valid schedule,
- the argument names of Hermes's `patch`, `read_file`, and `search_files` tools, which the guard checks (`path`, `mode`, `patch`), and the tool names in each role's allowlist (`ROLE_TOOLS` in `plugin/team_kit_guard/policy.py`).

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
