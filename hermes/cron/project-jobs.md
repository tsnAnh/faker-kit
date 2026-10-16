# Project bot scheduled jobs

`team-kit-admin add-project` creates these jobs, paused, in each project bot's profile from this file: each `##` heading is the job name, and its schedule, delivery, command, and output come from the lines under it. `{bot}` becomes the bot's profile name. Edit them here, then run `hermes/install.sh` again; it rewrites each job's script, creates jobs that are missing, turns an older job that still runs a model into a script job in place, and leaves each job's paused or active state as it is. For `team-kit-tick`, `team-kit-incidents`, and `team-kit-security`, `add-project --tick-every`, `--incidents-every`, and `--security-every` replace the default schedules and reschedule existing jobs in place with `hermes cron edit --schedule`, so a job keeps its id and stays active or paused as it was. `team-kit-admin activate` resumes them. Times are in the profile's `timezone`, Asia/Saigon.

Each job is a Hermes `--no-agent` job: no model runs in it. `team-kit-admin` writes one script per job to `scripts/team-kit-jobs/<job>.sh` in the bot's profile. The script runs the `Command` with the installed `team-kit-gh` wrapper, which does the work in code. With `Output: print` its output is delivered as it is; with `Output: silent` nothing is delivered on success. A refusal is delivered as one line, `team-kit notice: <command> refused: <reason>`. Any other failure, such as a missing wrapper, delivers the same line and also exits non-zero, so Hermes marks the run failed. A `team-kit-*` job no longer in this file is paused, for the owner to delete. Each job delivers to `bot-chat:{bot}`, the bot's own Bot Chat in Hermes Desktop. Every delivered line starts with `team-kit notice:`, and the guard taints that turn, so the bot cannot act on it without the owner. A run that prints `[SILENT]`, or nothing, delivers nothing.

An earlier version ran each command through a model turn with a prompt. The model sometimes rewrote the command, for example adding a fallback after `||`, and the guard refused the whole line, so the tick stopped.

## team-kit-tick

Schedule: `every 30m`
Deliver: `bot-chat:{bot}`
Command: `team-kit-gh tick`
Output: `print`

Launches queued units whose dependencies merged and that fit under the limits, starts signed read-only pull request reviews when unit PR heads appear or move, turns a finished agent's report into "ready to merge" or a gate, and frees the slot either way.

## team-kit-status

Schedule: `0 9 * * 1`
Deliver: `bot-chat:{bot}`
Command: `team-kit-gh status`
Output: `print`

## team-kit-security

Schedule: `every 15m`
Deliver: `bot-chat:{bot}`
Command: `team-kit-gh security`
Output: `silent`

When the default branch has moved since the last review, and no review is running, starts one cloud session that reviews the new commits for security issues, with the `review-main-security` skill. It changes nothing and posts a signed result on the project's `agent:security` issue. `team-kit-tick` reads the result and sends a notice only for high or medium findings.

## team-kit-incidents

Schedule: `every 10m`
Deliver: `bot-chat:{bot}`
Command: `team-kit-gh incidents`
Output: `print`

Polls the default branch's checks, the health URLs, and the external alert sources in `.agents/dispatch.json`, from this machine, outbound only. A failure opens an `agent:incident` issue, starts a triage session that changes nothing, and tells the owner; a pass closes it again.

## team-kit-upstream

Schedule: `0 9 * * 1`
Deliver: `bot-chat:{bot}`
Command: `team-kit-gh upstream`
Output: `print`

Only for a repository whose `.agents/dispatch.json` sets `upstream_sync: true`, such as the kit itself. When the upstream repository named in `upstream/source.json` has moved past its pinned commit and no sync unit is open, it dispatches one unit from a fixed brief: a cloud agent runs `scripts/sync-upstream.mjs` and opens the pull request, which is verified and reviewed like any unit. Every other repository stays silent.

