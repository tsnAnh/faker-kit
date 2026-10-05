# Hermes Dispatch E2E Runbook

This artifact proves the live Hermes dispatch loop on a scratch repository:

1. install this checkout into a temporary Hermes home;
2. add one project bot for the scratch repository;
3. dispatch one tiny README-only unit;
4. wait for launch, a `done` report, a verifier verdict, risk label, and ready-to-merge notice;
5. raise and clear the project andon cord once;
6. stop before any merge.

## Authorization Boundary

Run this only against a disposable scratch repository that the owner explicitly names for this test. The script has no default repository and requires `--confirm-scratch-repo OWNER/REPO` to match `--repo OWNER/REPO`.

The script never runs `gh pr merge`, never pushes to `main` or `master`, never force-pushes, and never writes secrets. It leaves the test pull request open for the owner to inspect or close unmerged.

Do not run it against a production repository.

## Required Scratch Repo State

Before the live run, the scratch repository must already be dispatch-ready:

- `.agents/dispatch.json` exists on the default branch.
- `.agents/dispatch.md` exists on the default branch.
- `.agents/team-kit/KIT.json` exists on the default branch.
- Both `kit_ref` values match this checkout's current commit.
- `trusted_dispatchers` in `.agents/dispatch.json` includes the GitHub login that will run `gh`.
- `verifier_backend` is not `none`.
- `README.md` exists.
- The selected cloud backend is authenticated on this machine.

If any of these are missing, the script stops with a preflight error. It does not open setup or vendoring pull requests because those require owner review and merge.

## Inputs

```bash
python3 hermes/e2e/run_dispatch_e2e.py \
  --repo OWNER/REPO \
  --confirm-scratch-repo OWNER/REPO \
  --owner-login YOUR-GITHUB-LOGIN \
  --backend claude
```

Options:

- `--trusted-author LOGIN`: add another login whose reports and verifier verdicts count.
- `--backend codex --codex-env ENV_ID`: use Codex cloud launch arguments.
- `--timeout-minutes N`: overall wait for report, verifier, and ready notice. Default: `120`.
- `--poll-seconds N`: poll cadence. Default: `60`.
- `--keep-home`: preserve the temporary home for inspection.
- `--dry-run`: validate arguments and print the plan without touching anything.

## What It Changes

Local machine:

- Creates a temporary directory containing `home/.hermes`.
- Runs `hermes/install.sh --source <this checkout> --no-gateway`.
- Writes the temporary `guard.yaml` trusted authors.
- Creates temporary Hermes profiles under that temporary home.

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

Without `--keep-home`, the temporary home is removed automatically after the script exits.

## Troubleshooting

- `kit commit ... differs from kit_ref`: run the normal Hermes vendoring flow for the scratch repo, merge that setup PR yourself, then rerun this e2e.
- `dispatch did not launch immediately`: the cloud CLI is not authenticated, the repo does not trust the dispatching login, a limit is already full, or the andon cord is pulled.
- `unit gated`: read the issue linked in the notice. The script stops rather than launching fallback work.
- `ready notice has no verifier verdict`: the scratch repo has verification disabled; set `verifier_backend` to `claude` or `codex`.
- No `risk:*` label: inspect the pull request and `team-kit-gh tick` output; the e2e requires the real risk label before it passes.
