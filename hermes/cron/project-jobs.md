# Project bot scheduled jobs

`team-kit-admin add-project` creates both jobs, paused, in each project bot's profile from this file: each `##` heading is the job name, and its schedule, delivery, skill, and prompt come from the lines and the `text` block under it. `{bot}` becomes the bot's profile name. Edit them here, then run `hermes/install.sh` again; it creates jobs that are missing and leaves existing ones, and their paused or active state, as they are. `team-kit-admin activate` resumes them. Times are in the profile's `timezone`, Asia/Saigon.

Both jobs only run one `team-kit-gh` command, which does the work in code and prints what to deliver. The model makes no decision in them. Both deliver to `bot-chat:{bot}`, the bot's own Bot Chat in Hermes Desktop. Every delivered line starts with `team-kit notice:`, and the guard taints that turn, so the bot cannot act on it without the owner. A run that prints `[SILENT]` delivers nothing.

## team-kit-tick

Schedule: `every 30m`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

Launches queued units whose dependencies merged and that fit under the limits, turns a finished agent's report into "ready to merge" or a gate, and frees the slot either way.

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
