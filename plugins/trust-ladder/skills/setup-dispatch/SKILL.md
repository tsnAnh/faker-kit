---
name: setup-dispatch
description: Prepare one repository for team-kit dispatch, so a Hermes project bot can brief cloud coding agents there and the owner only merges. The repository side only. Writes .agents/dispatch.json (backend, kit commit, in-flight limit, forbidden paths, trusted dispatchers) and .agents/dispatch.md (standing orders), fills in the kit setup the repository still lacks, and in the project's home repository writes PROJECT.md, all in one pull request. Usually run by the cloud session the Hermes bootstrap bot starts, which passes every value in its prompt. Use for /setup-dispatch, "set up this repo for Hermes dispatch", or after a dispatch refusal about the kit pin or dispatch.json.
---

# Set up dispatch in a repository

Dispatch has two sides, and both run the same kit commit.

- **The repository**, this skill. It holds `.agents/dispatch.json`, `.agents/dispatch.md`, and, in the project's home repository, `PROJECT.md`.
- **The Hermes machine**, set up by the `hermes/` module of the kit: `hermes/install.sh` once per machine, then the bootstrap bot per project. It labels the repository, points its webhook at the machine, and protects its default branch with `team-kit-admin`. See [hermes/README.md](https://github.com/tsnAnh/team-kit/blob/main/hermes/README.md).

You need these values. The bootstrap bot's prompt gives them all. If you run this skill by hand, ask the owner for each:

| Value | What it is |
|---|---|
| `backend` | `claude` (Claude Code cloud, started with `claude --cloud`) or `codex` (Codex cloud, started with `codex cloud exec`) |
| `kit_ref` | The full 40-character kit commit the Hermes machine runs |
| `trusted_dispatchers` | The GitHub logins that open unit issues: the project bot's login, and the owner's |
| `project` | The project's slug |
| `home repository` | The `OWNER/REPO` that holds `PROJECT.md` for the whole project |
| `codex_env` | For `codex` only: this repository's Codex cloud environment id |

Open exactly one pull request. Never merge it, and never push to the default branch.

## 1. Check the ground

The repository must work for unattended cloud agents:

- `setup-team-kit` is done, so the kit loads in every session here.
- `setup-cloud-agents` is done: `scripts/check.sh` exists and passes in a fresh cloud session. For Codex, the environment's setup script runs `install-kit-skills.sh --ref <kit_ref>`.
- CI runs `scripts/check.sh` on every pull request.

If one is missing, do that skill now, on the same branch, so the one pull request carries it. If it cannot be done from here (a Codex environment setting, a CI secret), do the rest and list what is left under "Owner to do" in the pull request.

## 2. Write `.agents/dispatch.json`

Copy `templates/dispatch/dispatch.json` and fill it:

- `backend` and `kit_ref` from the values.
- `max_in_flight`: how many units may run here at once. Start at 1 or 2. The machine has its own total across projects.
- `forbidden_paths`: paths no unit may touch. Keep the template's and add the repository's own, such as generated code or vendored files.
- `trusted_dispatchers` from the values. Coding agents follow only issues these logins wrote.
- `codex_env` for `codex`. Delete the key for `claude`.

A Codex cloud task cannot push or open a pull request unless its environment allows it. Until a pull request with the marker exists, the unit stays "waiting for PR". Say so in the pull request for `codex`.

Claude Code cloud sessions load the kit from the marketplace in `.claude/settings.json`, which follows the kit's default branch. `kit_ref` must be a commit on that branch.

## 3. Write `.agents/dispatch.md`

Copy `templates/dispatch/dispatch.md`. Add standing orders only the repository's own files justify, such as "Every API change updates `openapi.yaml`". Every brief pastes this file verbatim, so keep it short.

## 4. In the home repository only, write `PROJECT.md`

If this repository is the home repository, write `PROJECT.md` at its root from `templates/dispatch/PROJECT.md`:

- The project's purpose in two sentences, from the README.
- Each repository of the project, one line each: what it is and where its contract lives (an OpenAPI file, a schema, an events package). Only list repositories you were given.
- How they talk: who calls whom, over what.
- `docs/decisions/`: create it with a `README.md` saying each cross-repository decision is one file, `NNNN-title.md`, written by a contract unit before any repository implements it.

If this is not the home repository, add one line to its `AGENTS.md`: "This repository is part of project `<project>`; see `PROJECT.md` in `<home repository>`."

## 5. Open the pull request

One branch, such as `team-kit/setup-dispatch`, and one pull request to the default branch. Its body lists:

- the values used,
- every file it adds or changes,
- the CI check name that runs `scripts/check.sh`, which the bootstrap bot makes required with `team-kit-admin protect`,
- "Owner to do", for anything this session could not do.

Run `scripts/check.sh` before you push, and put its result in the pull request.

## Report

The pull request link, the CI check name, and the "Owner to do" list.
