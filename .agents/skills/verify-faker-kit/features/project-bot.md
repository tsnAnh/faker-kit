# Set up a project and run its bot's commands

`faker-kit add-project` creates a project bot for one repository: its profile, agent jobs, the andon label, and the launch checkout Claude Code sessions start from. The bot then runs `faker-kit launch`, `merge`, `status`, `andon`, `incidents`, and `issues`.

## Sub-features

- `bot-launch`: `launch` starts a cloud session with the brief as its first message, refuses a brief that does not start with `/faker-mode`, one outside the bot's briefs folder, a secret in a brief, a vendored kit that is not this machine's, and any launch while the andon cord is pulled.
- `bot-merge`: `merge` refuses on failed or pending checks, a missing or older-head approval, `Risk: high` or no Risk line, a failed, missing, or forged `security-review` (the linked review-workflow run on the head has no successful `security` job), a change to a workflow, guard, or protected path, a person's changes-requested review, a fork, and a conflict; it tells the owner, then opens a one-hour window on the first passing call (and opens none when the owner cannot be told), and merges only the exact head after it; a new head restarts the window. In a repository without the review workflow yet, `status` and `merge` report `security-review` as not reported instead of failing.
- `bot-incidents`: `incidents` polls checks, health URLs, and Sentry, PagerDuty, Datadog, and Grafana alerts with tokens from the bot's `.env`, lists each new incident once, and a triage session launches from the brief template.
- `bot-bug-reports`: `issues` lists an issue only when a collaborator with write access added its `faker:fix` label; a fix session launches from the brief template; `add-project` creates the three labels.
- `bot-andon`: `andon raise` and `clear` for the project, and the machine cord read at launch.

## How to get to it (user POV)

- The owner runs `faker-kit add-project OWNER/REPO` (or asks the bootstrap bot, or runs `/setup-hermes-project`).
- The project bot runs `faker-kit <command>` from its Hermes profile; its jobs run the same skill.

## Driving it

`python3 hermes/e2e/run_dispatch_e2e.py --artifact <path>`. Proof: every "launch", "merge", "window", and andon step prints `ok`, and two runs print the same digest. The "guard:" steps are the guard's verdicts through the real plugin (each call's verdict is in the transcript's `verdicts`), the "runner" steps are the CI slots, and the "vendor" steps are `faker-kit vendor` and `vendor_kit.py`.

## Gotchas

- Without `HERMES_HOME` set to the bot's profile, `faker-kit` refuses project commands: it only runs them as a project bot.
- The stubs answer only the calls the e2e models. A command that needs another call fails loudly; extend the stub the way the real CLI answers, not to make the proof pass.
