# team-kit for Hermes

This module runs team-kit dispatch on a [Hermes Agent](https://github.com/NousResearch/hermes-agent) machine: one bootstrap bot that sets projects up, and one project bot per project that briefs cloud coding agents (Claude Code or Codex) and tracks their pull requests until the owner merges. How to use it is in [Dispatch work with Hermes](../docs/how-to/dispatch-with-hermes.md).

It lives outside `plugins/` on purpose. Nothing here loads in a coding agent: plugins are what coding agents load, and this is what the Hermes bots load. The coding agents keep using the `trust-ladder` skills `setup-dispatch` and `follow-brief`, and the brief templates in `plugins/trust-ladder/templates/dispatch/`.

## Install

```bash
bash hermes/install.sh --ref <kit commit> --bootstrap-bot <name>
```

Run `bash hermes/install.sh --help` for every flag. Then set `trusted_authors`, `max_in_flight_total`, and `webhook_base_url` in `~/.hermes/team-kit/guard.yaml`, and ask the bootstrap bot for your first project.

## Layout

| Path | What it is | Installed to |
|---|---|---|
| `install.sh` | Fetches the kit at a commit, writes the wrappers, refreshes every bot | — |
| `bin/team_kit_admin.py` | `team-kit-admin`: the bootstrap bot's only command, and the installer's helper | `~/.hermes/bin/team-kit-admin` (a wrapper) |
| `bin/team_kit_gh.py` | `team-kit-gh`: a project bot's only GitHub access | `~/.hermes/bin/team-kit-gh` (a wrapper) |
| `lib/team_kit_contracts.py` | The brief, report, PR, and launch markers, and the cloud command lines | read from the kit checkout |
| `lib/team_kit_cloud.py` | Starts a `claude --cloud` or `codex cloud exec` session and waits for its id | read from the kit checkout |
| `lib/team_kit_triage.py` | Webhook triage: keeps only events that change one of the bot's units | each project bot's `scripts/team-kit/` |
| `lib/install_config.py` | Reads Hermes config, writes it only through `hermes config set` | read from the kit checkout |
| `plugin/team_kit_guard/` | The Hermes plugin that checks every tool call against `guard.yaml` and `bots.yaml` | each bot's `plugins/` |
| `profiles/bootstrap/`, `profiles/project/` | `SOUL.md` and the config merged into each bot's profile | each bot's profile |
| `skills/bootstrap/`, `skills/dispatch/` | The bots' skills | linked into each bot's `skills/` |
| `cron/project-jobs.md` | `team-kit-drain` and `team-kit-status`, created paused per project bot | each project bot's cron |
| `webhook/route.yaml`, `webhook/github-triage.py` | One webhook route per project, and its entry point with the bot's name filled in | the default profile's webhook config, and the bot's `scripts/team-kit/` |
| `templates/guard.yaml` | The machine's policy, written once | `~/.hermes/team-kit/guard.yaml` |
| `tests/` | `python -m pytest hermes` | — |

Under `~/.hermes/team-kit/`:

- `kit/`, the kit at the pinned commit. Every bot's files come from here.
- `guard.yaml`, the policy. You edit it.
- `bots.yaml`, each bot's role, project, repositories, and home repository. Only `team-kit-admin` writes it.
- `webhook-secret`, generated once, mode 600. It signs every project's route.
- `briefs/<bot>/`, the only place a project bot may write.
- `checkouts/`, shallow clones `claude --cloud` starts from. Pruned after 3 days.
- `pr-cards/`, which pull request belongs to which card, for check events.
- `in-flight/<bot>.json`, each project bot's count of running units, for `max_in_flight_total`.

## What only you do

- Merge pull requests.
- Put each bot's GitHub token in its profile's `.env`. No bot ever sees a token in chat.
- Sign the coding agent in on the machine.
- Approve each `team-kit-admin` command the bootstrap bot runs.

## Still to check in the first pilot

These depend on Hermes and the cloud CLIs, and the tests fake them:

- that a profile's `.env` (`GH_TOKEN`, `HERMES_KANBAN_BOARD`) reaches its terminal, Kanban tools, and cron runs,
- the session line `claude --cloud` and `codex cloud exec` print, which `team_kit_cloud.py` waits for,
- that the ids `hermes cron list --all` prints are what `hermes cron resume` takes,
- that Kanban's completion contract reads checks with the project bot's login,
- that the shared gateway picks up a new project's route after `team-kit-admin add-project` restarts it,
- the argument names of Hermes's `patch`, `read_file`, and `search_files` tools, which the guard checks (`path`, `mode`, `patch`), and the tool names in each role's allowlist (`ROLE_TOOLS` in `plugin/team_kit_guard/policy.py`).

## Migrate from version 1

Version 1 had two fixed profiles, `dispatcher` and `launcher`, and listed repositories in `guard.yaml`. Version 2 has a bootstrap bot and a bot per project, with roles in `bots.yaml`. The guard refuses a version 1 `guard.yaml`, so every tool call is blocked until you migrate.

1. Pause the old jobs: `hermes -p dispatcher cron list --all`, then `hermes -p dispatcher cron pause <id>` for `dispatcher-drain` and `dispatch-status`.
2. Let in-flight units finish, or note their issues. Version 2 counts in-flight units by the `agent:running` label, which version 1 never set.
3. In `~/.hermes/team-kit/guard.yaml`: set `version: 2`, delete `repos` and `profiles`, and add `max_in_flight_total: 3`. Keep `trusted_authors`, `deny`, `untrusted_readers`, and `read_only_tools`. Compare with `templates/guard.yaml`.
4. Run `bash hermes/install.sh --ref <new kit commit> --bootstrap-bot <name>`.
5. Ask the bootstrap bot to set up a project with the old repositories. It creates a new project bot with its own route, so point each repository's webhook at the new URL (`add-repo` does it when `webhook_base_url` is set) and delete the old `/p/dispatcher/webhooks/team-kit-github` hook. Run `bootstrap-repo` only for repositories without `.agents/dispatch.json`; for the rest, move `kit_ref` to the new commit in a pull request.
6. Move the dispatcher's `GH_TOKEN` into the new project bot's `.env` if it should keep the same login, then `team-kit-admin activate`.
7. Once nothing uses them, delete the `dispatcher` and `launcher` profiles with `hermes profile delete`, and the `team-kit-github` route from the default profile's `platforms.webhook.extra.routes`.
