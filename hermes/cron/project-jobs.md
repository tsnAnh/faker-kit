# Project bot cron jobs

`team-kit-admin add-project` creates both jobs, paused, in each project bot's profile from this file: each `##` heading is the job name, and its schedule, delivery, skill, and prompt come from the lines and the `text` block under it. `{bot}` becomes the bot's profile name. Edit them here, then run `hermes/install.sh` again; it creates jobs that are missing and leaves existing ones, and their paused or active state, as they are. `team-kit-admin activate` resumes them. Times are in the profile's `timezone`, Asia/Saigon.

Both jobs deliver to `bot-chat:{bot}`, the bot's own Bot Chat in Hermes Desktop. Hermes cron cannot target any other named Desktop session. Bot Chat delivery is a new turn the bot answers, so every delivered line starts with `team-kit notice:`, and the guard taints that turn. A run that prints `[SILENT]` delivers nothing.

## team-kit-drain

Schedule: `every 2h`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

The bot's `team-kit-<project>` webhook route fires this job for every event `github-triage.py` keeps, with the event's fields as context. The schedule is a safety net for events GitHub failed to deliver, and it launches units that were waiting for a dependency or a free slot.

```text
Run the drain procedure of the dispatch skill (references/drain.md).
Start with `team-kit-gh project`.
If this run carries a team-kit unit event, handle it first, then sweep every open unit
and launch every queued unit that can start.
If it carries none, only sweep and launch.
Never run `team-kit-gh read` in this job.
Print [SILENT] unless a human gate opened. Otherwise print only the gate lines,
each starting with "team-kit notice:".
```

## team-kit-status

Schedule: `0 9,13,17 * * *`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`

```text
Write the status digest of the dispatch skill: exactly three lines,
In flight, Waiting on you, and Done since last, each starting with "team-kit notice:",
with units grouped by repository inside each line.
Use only kanban_list and kanban_show.
```
