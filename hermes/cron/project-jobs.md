# Project bot scheduled jobs

`team-kit-admin add-project` creates these jobs, paused, in each project bot's profile from this file: each `##` heading is the job name, and its schedule, delivery, skill, and prompt come from the lines and the `text` block under it. `{bot}` becomes the bot's profile name. Edit them here, then run `hermes/install.sh` again; it creates jobs that are missing and leaves existing ones, and their paused or active state, as they are. For `team-kit-tick`, `team-kit-incidents`, and `team-kit-security`, `add-project --tick-every`, `--incidents-every`, and `--security-every` replace the default schedules and reschedule existing jobs in place with `hermes cron edit --schedule`, so a job keeps its id and stays active or paused as it was. `team-kit-admin activate` resumes them. Times are in the profile's `timezone`, Asia/Saigon.

Each job only runs one `team-kit-gh` command, which does the work in code and prints what to deliver. The model makes no decision in them. Each delivers to `bot-chat:{bot}`, the bot's own Bot Chat in Hermes Desktop. Every delivered line starts with `team-kit notice:`, and the guard taints that turn, so the bot cannot act on it without the owner. A run that prints `[SILENT]` delivers nothing.

## team-kit-tick

Schedule: `every 30m`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

Launches queued units whose dependencies merged and that fit under the limits, starts signed read-only pull request reviews when unit PR heads appear or move, turns a finished agent's report into "ready to merge" or a gate, and frees the slot either way.

```text
Run `team-kit-gh tick` in the terminal. Print its output exactly as it is, and nothing else.
If it refuses, print one line: "team-kit notice: tick refused: " and the refusal.
```

## team-kit-status

Schedule: `0 9,13,17 * * *`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

```text
Run `team-kit-gh status` in the terminal. Print its output exactly as it is, and nothing else.
If it refuses, print one line: "team-kit notice: status refused: " and the refusal.
```

## team-kit-security

Schedule: `every 15m`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

When the default branch has moved since the last review, and no review is running, starts one cloud session that reviews the new commits for security issues, with the `review-main-security` skill. It changes nothing and posts a signed result on the project's `agent:security` issue. `team-kit-tick` reads the result and sends a notice only for high or medium findings.

```text
Run `team-kit-gh security` in the terminal. If it succeeds, print exactly `[SILENT]` and nothing else.
If it refuses, print one line: "team-kit notice: security refused: " and the refusal.
```

## team-kit-incidents

Schedule: `every 10m`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

Polls the default branch's checks, the health URLs, and the external alert sources in `.agents/dispatch.json`, from this machine, outbound only. A failure opens an `agent:incident` issue, starts a triage session that changes nothing, and tells the owner; a pass closes it again.

```text
Run `team-kit-gh incidents` in the terminal. Print its output exactly as it is, and nothing else.
If it refuses, print one line: "team-kit notice: incidents refused: " and the refusal.
```

## team-kit-upstream

Schedule: `0 9 * * 1`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

Only for a repository whose `.agents/dispatch.json` sets `upstream_sync: true`, such as the kit itself. When the upstream repository named in `upstream/source.json` has moved past its pinned commit and no sync unit is open, it dispatches one unit from a fixed brief: a cloud agent runs `scripts/sync-upstream.mjs` and opens the pull request, which is verified and reviewed like any unit. Every other repository stays silent.

```text
Run `team-kit-gh upstream` in the terminal. Print its output exactly as it is, and nothing else.
If it refuses, print one line: "team-kit notice: upstream refused: " and the refusal.
```
