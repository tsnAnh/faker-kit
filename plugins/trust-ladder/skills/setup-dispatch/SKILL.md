---
name: setup-dispatch
description: Set up Hermes dispatch for one repository, so the owner states work in Hermes Desktop and cloud coding agents turn it into pull requests the owner only merges. Covers both sides. In the repository it picks the backend (Claude Code or Codex) once, writes .agents/dispatch.json and .agents/dispatch.md in a pull request, and turns on branch protection with required checks and the two labels. On the Hermes machine it runs hermes/install.sh pinned to the same kit commit, fills guard.yaml, sets models and GitHub logins per profile, points a GitHub webhook at the gateway, and proves the loop with one small unit. Use for /setup-dispatch, "set up Hermes dispatch", "let Hermes hand work to cloud agents", or after a dispatch refusal about the kit pin or the repository list.
---

# Set up dispatch

Dispatch has two sides, and both must run the same kit commit.

- **The repository.** Holds `.agents/dispatch.json` and `.agents/dispatch.md`, branch protection with required checks, and the labels. Do this half in Claude Code or Codex in a clone of the repository.
- **The Hermes machine.** Holds the `dispatcher` and `launcher` profiles, the guard, the webhook, and the cron jobs. Do this half in a terminal on that machine, or in Hermes's default profile. The `dispatcher` profile cannot help, because its guard allows only `team-kit-gh`.

How the parts fit, and what each safety layer does, is in [Dispatch with Hermes](https://github.com/tsnAnh/team-kit/blob/main/docs/how-to/dispatch-with-hermes.md).

## 0. Check the ground

The repository must already work for unattended cloud agents:

- `setup-team-kit` is done, so the kit loads in every session there.
- `setup-cloud-agents` is done, and a fresh cloud session passes `scripts/check.sh`. For Codex, the environment exists and its setup script runs `install-kit-skills.sh`.
- CI runs `scripts/check.sh` on every pull request.

If one is missing, do that skill first and stop here.

## 1. Pick the backend once

Ask the owner which coding agent runs the units: `claude` (Claude Code cloud sessions, started with `claude --cloud`) or `codex` (Codex cloud tasks, started with `codex cloud exec`). One backend per repository. Changing it later is a pull request to `.agents/dispatch.json`.

For `codex`, get the Codex cloud environment id for this repository. It goes in `codex_env`.

A Codex cloud task cannot push or open a pull request unless its environment allows it. Until a pull request with the marker exists, the card stays blocked "waiting for PR", and the owner opens the pull request from the task. Say so to the owner before they pick Codex.

## 2. Pick the kit commit

Pick one full 40-character commit of the kit. Both sides run it.

- `kit_ref` in `.agents/dispatch.json` is that commit.
- The Hermes install runs with `--ref` set to that commit.
- Codex cloud: the environment's setup script runs `install-kit-skills.sh --ref` with that commit.
- Claude Code cloud sessions load the kit from the marketplace in `.claude/settings.json`, which follows the kit's default branch. Pick a commit on that branch, and move `kit_ref` when the team takes a newer kit.

`team-kit-gh` refuses to open an issue while this machine's kit commit does not start with `kit_ref`.

## 3. Write the repository config in a pull request

On a new branch, never the default branch:

1. Copy `templates/dispatch/dispatch.json` to `.agents/dispatch.json` and fill it:
   - `backend`: `claude` or `codex`.
   - `kit_ref`: the commit from step 2.
   - `max_in_flight`: how many units may run at once. Start at 1 or 2.
   - `forbidden_paths`: paths no unit may touch. Keep the template's and add the repository's own.
   - `trusted_dispatchers`: the GitHub login the dispatcher profile opens issues as. Coding agents follow only issues this login wrote.
   - `codex_env`: the Codex environment id. Delete the key for `claude`.
2. Copy `templates/dispatch/dispatch.md` to `.agents/dispatch.md`. Edit the standing orders with the owner. Every brief pastes them verbatim.
3. Open a pull request and ask the owner to merge it. The dispatcher reads both files from the default branch.

## 4. Protect the default branch

Kanban completes a unit's card only when the repository's required checks pass on the pull request's head. With no required checks, no card ever completes.

- Turn on branch protection from `templates/branch-protection.md`. "Require status checks to pass" must name the CI job that runs `scripts/check.sh`.
- Keep "Do not allow bypassing" on. The owner merges through the same gates.

## 5. Create the labels

```bash
gh label create agent-task -R OWNER/REPO --description "A unit dispatched by Hermes"
gh label create agent:needs-human -R OWNER/REPO --description "A dispatched unit needs the owner"
```

These are the only two labels dispatch uses.

## 6. Install the Hermes side

Use an always-on machine, such as a home server or a small VM. Cron, the webhook, and the Kanban dispatcher run only while the gateway runs. A laptop that sleeps drops events. Hermes Desktop can run anywhere and connect to it.

From a clone of the kit on that machine:

```bash
bash plugins/trust-ladder/hermes/install.sh --ref <kit commit> \
  --dispatcher-model <strong model> --launcher-model <cheap model>
```

`--ref` must be the same full commit as `kit_ref`. `--source` points at another kit repository or directory. The script:

- fetches the kit at that commit into `~/.hermes/team-kit/kit`,
- links only `dispatch` and `setup-dispatch` into Hermes's skills,
- creates the `dispatcher` and `launcher` profiles if missing and merges their config,
- installs and enables the `team_kit_guard` plugin in both profiles,
- installs `team-kit-gh` at `~/.hermes/bin/team-kit-gh`, and the webhook script in the dispatcher's `scripts/team-kit/`,
- writes a sample `~/.hermes/team-kit/guard.yaml` once, and never overwrites it,
- generates the webhook secret into `~/.hermes/team-kit/webhook-secret`,
- adds the `team-kit-github` webhook route and the `dispatcher-drain` and `dispatch-status` cron jobs,
- installs and starts the dispatcher's gateway.

The Hermes side never gets faker-mode, lint packs, or any skill that writes code.

To install only the guard plugin from Desktop instead, use Capabilities, then Plugins, then Install from Git. Source is `tsnAnh/team-kit/plugins/trust-ladder/hermes/plugin/team_kit_guard`, and Pin to commit is the same 40-character commit. The command line equivalent:

```bash
hermes -p dispatcher plugins install tsnAnh/team-kit/plugins/trust-ladder/hermes/plugin/team_kit_guard --ref <kit commit> --enable
```

Do the same for `-p launcher`.

## 7. Fill guard.yaml

Edit `~/.hermes/team-kit/guard.yaml` (sample in `templates/dispatch/guard.yaml`):

- `repos`: every `OWNER/REPO` Hermes may dispatch to. The webhook ignores every other repository, and `team-kit-gh` refuses them.
- `trusted_authors`: the GitHub logins whose unit pull requests and reports count. Add the `trusted_dispatchers` login, which opens the issues, and the login the coding agent posts as. For Claude Code on the web that is usually the owner. The webhook ignores pull requests and reports by anyone else, so a copied marker cannot move a card.
- Keep `version: 1`, `require_brief_marker: true`, the `deny` list, and `untrusted_readers`.
- To tie the launcher to one repository, replace `[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+` in both `launcher.terminal_allow` patterns with the repository's name, dots escaped (`acme/shop\.web`).

If this file is missing or does not parse, the guard blocks every tool call in both profiles. That is on purpose.

## 8. Set models

The dispatcher plans and writes briefs, so give it a strong model. The launcher only runs a command it is given, so give it a cheap one. Set them with the install flags, or later:

```bash
hermes -p dispatcher config set model.default <strong model>
hermes -p launcher config set model.default <cheap model>
```

## 9. Give each profile its own GitHub login

Each profile reads `GH_TOKEN` from its own `.env` in `~/.hermes/profiles/<profile>/.env`.

- **dispatcher.** A fine-grained token for the repositories in `repos`, with Issues read and write and Contents read. Its login is the one in `trusted_dispatchers`.
- **launcher.** A read-only token, with Contents, Pull requests, Checks, and Commit statuses read. Kanban reads required checks as the card's assignee, which is the launcher. If the required checks come from a classic branch protection rule instead of a ruleset, add Administration read so it can see them.

The launcher also needs the coding agent's own login on this machine. For `claude`, sign in so `claude --cloud` starts a session. For `codex`, sign in so `codex cloud exec` starts a task.

## 10. Point a GitHub webhook at the gateway

The gateway listens on port 8644. GitHub must reach it over HTTPS through a tunnel or a reverse proxy with TLS, such as Cloudflare Tunnel or Tailscale Funnel. Expose only that port.

In the repository's settings, add a webhook:

- Payload URL: `https://<public host>/webhooks/team-kit-github`.
- Content type: `application/json`.
- Secret: the contents of `~/.hermes/team-kit/webhook-secret`.
- Events: Pull requests, Issue comments, and Check suites only.

## 11. Keep approvals on

In both profiles, `approvals.mode` must stay `manual` or `smart`, and `approvals.cron_mode` must stay `deny`. With `off`, or with `--yolo`, Hermes approves the guard's approval requests by itself, and a turn that read untrusted text can act on it.

## 12. Prove it

Open the `dispatcher` profile in Hermes Desktop and walk one small unit through. Check each item.

- [ ] `curl http://localhost:8644/health` returns ok on the machine, and the webhook's Recent Deliveries in GitHub show a 2xx for its ping.
- [ ] `hermes -p dispatcher cron list` shows `dispatcher-drain` and `dispatch-status`.
- [ ] `team-kit-gh kit-check --repo OWNER/REPO` prints the kit commit and `kit_ref`, with no refusal.
- [ ] In the dispatcher, ask for one small change, such as a typo fix in the README. It restates one `small` unit and asks to confirm.
- [ ] After you confirm, an issue labeled `agent-task` opens, written by the `trusted_dispatchers` login, starting with `<!-- team-kit:brief v1 card=... -->`.
- [ ] The card shows a `launch:` comment, then a session URL or task id from the launcher, then blocks with "waiting for PR".
- [ ] A pull request opens with `<!-- team-kit:pr v1 card=<card> issue=<n> -->` and `Closes #<n>` in its body, and a report comment on the issue starts with `<!-- team-kit:report v1 -->` and `status: done`. For Codex, open the pull request from the task if it could not.
- [ ] When the required checks pass, the card completes with the pull request in `published_pr`.
- [ ] Ask the dispatcher to run `git status` in the terminal. It refuses, or the guard blocks it.
- [ ] You merge the pull request. The card gets a `merged:` comment, and the next status digest lists it as done.

## Report

The backend, the kit commit on both sides, the link to the config pull request, the required checks, the repositories in `guard.yaml`, the model per profile, the webhook's public URL, and the link to the proof pull request. Then tell the owner the daily routine. They state work in Desktop, answer gates in Bot Chat, and merge. Nothing else.
