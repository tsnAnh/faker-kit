# Hermes Dispatch E2E Runbook

This artifact proves the live Hermes dispatch loop on a scratch repository:

1. install this checkout into a temporary `TEAM_KIT_HOME` and Hermes home (your `HOME` is not changed);
2. add one project bot for the scratch repository;
3. dispatch one tiny README-only unit;
4. wait for launch, a `done` report, a verifier verdict, risk label, and ready-to-merge notice;
5. raise and clear the project andon cord once;
6. stop before any merge.

## Before You Run It

1. **For a scratch repository whose backend is `claude`, create its routine first.** At claude.ai/code/routines, create a routine on the scratch repository with the prompt `team-kit-admin add-project` prints, no connectors, and a one-off run date far in the future; turn it on, add an API trigger, and generate its token. Put the token in a second variable the same way as below (`read -rs HERMES_E2E_ROUTINE_TOKEN && export HERMES_E2E_ROUTINE_TOKEN`) and pass `--routine trig_... --routine-token-env HERMES_E2E_ROUTINE_TOKEN`.
2. **Create a token for the project bot, for the scratch repository only.** In GitHub, create a fine-grained personal access token whose repository access is "Only select repositories" and names just the scratch repository, with: Issues read and write; Contents, Pull requests, Checks, and Commit statuses read; Metadata read (added automatically). Prefer a separate bot account. Its login must be in `trusted_dispatchers` in the scratch repository's `.agents/dispatch.json`.
3. **Put it in an environment variable, from your own shell,** and pass only the variable's *name* to the script:

   ```bash
   read -rs HERMES_E2E_TOKEN && export HERMES_E2E_TOKEN
   ```

   Do not use `GH_TOKEN` or `GITHUB_TOKEN`: those are what your own `gh` login reads, and the project bot must not inherit your broad token. The script refuses those names, and refuses to run when the variable is unset or empty.
4. Sign in to `gh`, `claude`, and `codex` as usual. The script leaves `HOME` alone, so it sees the same logins you do.

## Authorization Boundary

Run this only against a disposable scratch repository that the owner explicitly names for this test. The script has no default repository and requires `--confirm-scratch-repo OWNER/REPO` to match `--repo OWNER/REPO`.

The script never runs `gh pr merge`, never pushes to `main` or `master`, never force-pushes, and writes the bot's token only to the temporary profile's `.env`. It leaves the test pull request open for the owner to inspect or close unmerged.

Do not run it against a production repository.

## Required Scratch Repo State

Before the live run, the scratch repository must already be dispatch-ready:

- `.agents/dispatch.json` exists on the default branch.
- `.agents/dispatch.md` exists on the default branch.
- `.agents/team-kit/KIT.json` exists on the default branch.
- Both `kit_ref` values match this checkout's current commit.
- `trusted_dispatchers` in `.agents/dispatch.json` includes the login of the project bot's token.
- `verifier_backend` is not `none`.
- `README.md` exists.
- The scratch repository's `backend` (in `.agents/dispatch.json`) is signed in on this machine. The script does not change that file; set `backend`, and `codex_env` for Codex, in the scratch repository yourself.

If any of these are missing, the script stops with a preflight error. It does not open setup or vendoring pull requests because those require owner review and merge.

## Inputs

```bash
python3 hermes/e2e/run_dispatch_e2e.py \
  --repo OWNER/REPO \
  --confirm-scratch-repo OWNER/REPO \
  --owner-login YOUR-GITHUB-LOGIN \
  --token-env HERMES_E2E_TOKEN
```

Options:

- `--token-env NAME` (required): the environment variable that holds the project bot's token. The script writes the token to the temporary profile's `.env` (mode 600), passes it to the bot's commands as `GH_TOKEN` in their environment only, replaces any token your shell already exports for `gh`, and never prints it or puts it in an argument, URL, or error message.
- `--routine trig_...` and `--routine-token-env NAME`: the scratch repository's Claude Code routine and the variable that holds its token, for a `claude` backend. The token goes only to the temporary profile's `.env` as `TEAM_KIT_ROUTINE_TOKEN`; it is never printed or put in an argument.
- `--trusted-author LOGIN`: add another login whose reports and verifier verdicts count.
- `--timeout-minutes N`: overall wait for report, verifier, and ready notice. Default: `120`.
- `--poll-seconds N`: poll cadence. Default: `60`.
- `--keep-home`: preserve the temporary home for inspection.
- `--dry-run`: validate arguments and print the plan without touching anything.

## What It Changes

Local machine:

- Creates a temporary directory containing `.hermes/`, used as `TEAM_KIT_HOME` (`.hermes/team-kit`) and as the Hermes home. `HOME` is not replaced.
- Runs `hermes/install.sh --source <this checkout> --no-gateway` with those two variables set, so nothing is written to your real `~/.hermes`.
- Writes the temporary `guard.yaml` trusted authors (you, the bot's login, and any `--trusted-author`).
- Creates temporary Hermes profiles there, and writes the bot's `GH_TOKEN` to its profile's `.env`.

Scratch repository:

- Creates Hermes labels through `team-kit-admin add-project`.
- Opens one `agent-task` issue.
- Starts one cloud coding session and one verifier session.
- The coding session opens one pull request that edits `README.md`.
- Adds a `risk:*` label and risk comment to that pull request.
- Opens and closes one `agent:andon` issue.

It does not merge the pull request.

## Cleanup

After a successful run:

1. Inspect the printed pull request URL.
2. Close the pull request unmerged, or merge it only if you intentionally want the README marker in the scratch repo.
3. Close the task issue if it remains open.
4. Delete the pull request branch if desired.
5. Remove the preserved temporary home if you used `--keep-home`.

Without `--keep-home`, the temporary directory, token included, is removed automatically after the script exits. Revoke the fine-grained token when you are done.

## Reading the Result

The script prints one line per step: `observed` only when a command's output showed it, otherwise `not seen`. Steps that wait on the cloud say how long it waited (`not seen (waited up to N min)`); steps after a failure say the run stopped before them. A step is never filled in from a later one. The run passes only when the done report, the verifier verdict, and the ready-to-merge notice were all seen; a verifier launch that no poll caught is shown as `not seen` but does not fail the run.

## Troubleshooting

- `kit commit ... differs from kit_ref`: run the normal Hermes vendoring flow for the scratch repo, merge that setup PR yourself, then rerun this e2e.
- `dispatch did not launch immediately`: the cloud CLI is not authenticated, the `claude` routine is missing, off, or has no token (`team-kit-admin status` shows `routine` and `routine_token`), the repo does not trust the dispatching login, a limit is already full, or the andon cord is pulled.
- `unit gated`: read the issue linked in the notice. The script stops rather than launching fallback work.
- `ready notice has no verifier verdict`: the scratch repo has verification disabled; set `verifier_backend` to `claude` or `codex`.
- No `risk:*` label: inspect the pull request and `team-kit-gh tick` output; the e2e requires the real risk label before it passes.
