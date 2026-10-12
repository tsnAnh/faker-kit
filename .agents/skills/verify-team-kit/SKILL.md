---
name: verify-team-kit
description: Verify a change to the team kit the way its users meet it. Drives the real Hermes module (install.sh, team-kit-admin, team-kit-gh) from this commit in an isolated sandbox with hermetic Hermes and GitHub doubles, proves the plugins load in Claude Code, Codex, and Pi, checks the upstream sync and the docs site, and keeps evidence after cleanup. Use to prove a team-kit pull request before reporting done, from a verify-unit session, or when asked to verify team-kit behavior.
---

# Verify the team kit

The team kit has no screen. Its users meet it in four places: the Hermes module an owner installs and runs (`hermes/install.sh`, `team-kit-admin`, `team-kit-gh`), the plugins agents load (Claude Code, Codex, Pi), the vendored upstream sync, and the docs site. Prove a change in the place it touches; `features/` maps each one.

`scripts/check.sh` is not verification. It proves the code passes its tests and linters. This skill proves a user gets the behavior: run both.

## Launch

Hermes and GitHub are external systems. The sandbox replaces exactly those two with the repository's own hermetic doubles (`hermes/tests/fake_hermes.py`, `hermes/tests/fake_gh.py`) on `PATH`; everything else is the real kit, installed from the committed `HEAD` by the real `install.sh`.

```bash
.agents/skills/verify-team-kit/scripts/sandbox.sh up
```

It prints the sandbox directory and writes `install.txt` under `.verify-evidence/<run>/`. It installs `HEAD`, not uncommitted edits: commit first (it warns when `hermes/` has uncommitted changes). Only one sandbox is up at a time; `up` refuses a second.

## Doctor

```bash
.agents/skills/verify-team-kit/scripts/sandbox.sh doctor
```

Healthy means: `team-kit-admin` is installed, `kit:` names this checkout's `HEAD` (short sha), `hermes` and `gh` resolve to the doubles inside the sandbox, and `team-kit-admin status` prints JSON with `trusted_authors: ["verify-bot", "verify-owner"]`. Run it first whenever anything looks off; never drive a sandbox doctor does not pass.

## Drive

Run commands inside the sandbox; its `HOME`, `PATH`, and `TEAM_KIT_HOME` are set, and your own Hermes, GitHub login, and `CLAUDE_CONFIG_DIR` are not visible:

```bash
.agents/skills/verify-team-kit/scripts/sandbox.sh run <command...>
.agents/skills/verify-team-kit/scripts/sandbox.sh evidence <name> <command...>   # also keeps the transcript
```

The installed commands are at `$HOME/.hermes/bin/` inside the sandbox; quote `$HOME` so the sandbox expands it: `sandbox.sh run bash -c '"$HOME/.hermes/bin/team-kit-admin" status'`. A project bot's commands need its profile and a token in the environment: `HERMES_HOME="$HOME/.hermes/profiles/<bot>" GH_TOKEN=verify-token`. The GitHub double serves repository files from `~/.fake-gh/files.json` (`{"OWNER/REPO": {"path": "content"}}`) and records every call in `~/.fake-gh/calls.log`; the Hermes double records calls in `~/.hermes/calls.log` and jobs in each profile's `jobs.json`.

Features without the Hermes module (plugin loading, upstream sync, docs site) run in this checkout directly; their files in `features/` say how.

## Evidence

Every proof is a transcript with the command, its full output, and its exit code, kept by `sandbox.sh evidence` in `.verify-evidence/<run>/<name>.txt` (ignored by git). Proof standards:

- Exercise the path a user takes: `install.sh`, `team-kit-admin`, and `team-kit-gh` as installed, never their Python functions directly.
- Capture the action and the resulting state: after a command, read the side effect it promises (labels in `~/.fake-gh/labels.json`, jobs in `jobs.json`, files under `~/.hermes/team-kit/`, the cloned launch checkout).
- The doubles stand in only for Hermes and GitHub. A change to how the kit talks to them (a new `gh` or `hermes` call) needs the double to model the real CLI first; say so in the verdict if it does not.
- Quote the evidence file paths, and the lines that prove each claim, in the verdict.

## Cleanup

```bash
.agents/skills/verify-team-kit/scripts/sandbox.sh down
```

It removes the sandbox directory it created and nothing else. The evidence stays in `.verify-evidence/<run>/`. Run `down` after every attempt, failed ones too.

## Features

See `features/README.md` for the map and the baseline every recipe starts from.
