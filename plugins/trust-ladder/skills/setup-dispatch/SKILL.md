---
name: setup-dispatch
description: Prepare one repository for team-kit dispatch, so a Hermes project bot can brief cloud coding agents there and the owner only merges. The repository side only; a project is one repository, which may hold several apps. Writes .agents/dispatch.json (backend, kit commit, in-flight limit sized by /trust-audit, forbidden paths, trusted dispatchers, verification skill) and .agents/dispatch.md (standing orders), makes sure AGENTS.md describes the repository's layout, and fills in the kit setup the repository still lacks, all in one pull request. Usually run by the cloud session the Hermes bootstrap bot starts, which passes every value in its prompt. Use for /setup-dispatch, "set up this repo for Hermes dispatch", or after a dispatch refusal about the kit pin or dispatch.json.
---

# Set up dispatch in a repository

Dispatch has two sides, and both run the same kit commit.

- **The repository**, this skill. It holds `.agents/dispatch.json` and `.agents/dispatch.md`.
- **The Hermes machine**, set up by the `hermes/` module of the kit: `hermes/install.sh` once per machine, then the bootstrap bot per project. It labels the repository and protects its default branch with `team-kit-admin`. See [hermes/README.md](https://github.com/tsnAnh/team-kit/blob/main/hermes/README.md).

A project is one repository. When a product has an API, a web app, and a mobile app, they live side by side in this one repository, so one agent can carry a feature through all of them in one pull request.

You need these values. The bootstrap bot's prompt gives them all. If you run this skill by hand, ask the owner for each:

| Value | What it is |
|---|---|
| `backend` | `claude` (Claude Code cloud, started with `claude --cloud`) or `codex` (Codex cloud, started with `codex cloud exec`) |
| `kit_ref` | The full 40-character kit commit the Hermes machine runs |
| `trusted_dispatchers` | The GitHub logins that open unit issues: the project bot's login, and the owner's |
| `project` | The project's slug |
| `codex_env` | For `codex` only: this repository's Codex cloud environment id |

Open exactly one pull request. Never merge it, and never push to the default branch.

## 1. Check the ground

The repository must work for unattended cloud agents:

- The kit is vendored at `kit_ref`: `.agents/team-kit/KIT.json` names that commit, and `python3 .agents/team-kit/vendor_kit.py --check .` passes. Cloud sessions get the kit's skills only from this copy. If it is missing or names another commit, stop: the owner vendors it first (the bootstrap bot's `team-kit-admin vendor`), because this session cannot fetch the kit. Never edit `.agents/team-kit/`.
- `setup-team-kit` is done.
- `setup-cloud-agents` is done: `scripts/check.sh` exists, runs the vendored kit's `--check`, and passes in a fresh cloud session.
- CI runs `scripts/check.sh` on every pull request.
- With several apps, `scripts/check.sh` and CI check only the apps a change touches (a workspace tool's affected-only run, or path filters), so a web change does not wait for the mobile build. The top trust layer needs `check` under 15 minutes.

If one is missing, do that skill or change now, on the same branch, so the one pull request carries it. If it cannot be done from here (a Codex environment setting, a CI secret), do the rest and list what is left under "Owner to do" in the pull request.

## 2. Describe the layout in `AGENTS.md`

The project bot plans from `AGENTS.md`. Make sure it has a short Layout section:

- Each app or package folder, one line each: what it is, such as `apps/api/` (the backend) or `apps/mobile/` (the iOS and Android app).
- How they talk: who calls whom, over what, and where the shared contract lives (an OpenAPI file, a schema, a shared types package).
- What a cloud session cannot prove, if anything, such as "iOS builds need macOS; cloud sessions run the mobile unit tests only".

Keep it to what an agent needs to plan a change. Do not repeat the README.

## 3. Write `.agents/dispatch.json`

Copy `templates/dispatch/dispatch.json` and fill it:

- `backend` and `kit_ref` from the values.
- `max_in_flight`: how many units may run here at once, from the repository's trust score. Run `/trust-audit` (Codex: `$trust-audit`) and use its lowest layer, as in [Run many agents](https://github.com/tsnAnh/team-kit/blob/main/docs/how-to/run-many-agents.md):

  | Lowest layer | `max_in_flight` |
  |---|---|
  | Below 2, or `scripts/check.sh` fails | 1 |
  | 2 or more | 3 to 10, more for faster `check` and fewer shared files |

  Put the score and the top items `/trust-audit` lists in the pull request, so the owner knows what raises the limit. The machine has its own total across projects.
- `forbidden_paths`: paths no unit may touch. Keep the template's and add the repository's own, such as generated code or vendored files. The lockfile stays forbidden, because parallel agents that each change it conflict.
- `trusted_dispatchers` from the values. Coding agents follow only issues these logins wrote.
- `verification_skill`: the name of the repository's verification skill, a `verify-*` folder under `.agents/skills/` or `.claude/skills/`. The project bot names it in every brief. If there is none and the repository has a user-facing surface, delete the key and list `/create-verification-skill` under "Owner to do": without it, agents prove changes with tests only, never on screen.
- `verifier_backend`: who verifies each pull request before the owner sees it, with the `verify-unit` skill. Use the other agent from `backend` when the owner has both (a second model family), else the same one. Set `none` only if the owner asks: then a unit goes to the owner on its own author's word. A `codex` verifier needs `codex_env` and an environment whose tasks may comment.
- `accept_verifications`: the verdicts that send a unit to the owner. Keep the template's `live-ui-verified` and `unit-test-verified`; add `type-check-only` only for a repository with nothing to run.
- `security_backend`: who reviews the default branch's new commits for security issues once a day, with the `review-main-security` skill. Usually the same as `verifier_backend`. Set `none` only if the owner asks. A `codex` reviewer needs `codex_env` and an environment whose tasks may comment.
- `codex_env` for `codex`. Delete the key for `claude`.

A Codex cloud task cannot push or open a pull request unless its environment allows it. Then the agent reports `partial`, which opens a gate, and the owner opens the pull request from the task. Say so in the pull request for `codex`.

`kit_ref` must equal the vendored kit's commit in `.agents/team-kit/KIT.json`; the vendored `--check` fails otherwise. To move to another kit commit later, vendor it again: that pull request updates both.

## 4. Write `.agents/dispatch.md`

Copy `templates/dispatch/dispatch.md`. Add standing orders only the repository's own files justify, such as "Every API change updates `openapi.yaml`". Every brief pastes this file verbatim, so keep it short.

## 5. Open the pull request

One branch, such as `team-kit/setup-dispatch`, and one pull request to the default branch. Its body lists:

- the values used, and the `/trust-audit` score behind `max_in_flight`,
- every file it adds or changes,
- the CI check name that runs `scripts/check.sh`, which the bootstrap bot makes required with `team-kit-admin protect`,
- "Owner to do", for anything this session could not do.

Run `scripts/check.sh` before you push, and put its result in the pull request.

## Report

The pull request link, the CI check name, the trust score, and the "Owner to do" list.
