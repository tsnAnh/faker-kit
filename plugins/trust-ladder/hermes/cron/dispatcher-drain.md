# Dispatcher cron jobs

`install.sh` creates both jobs in the `dispatcher` profile from this file: each `##` heading is the job name, and its schedule, delivery, skill, and prompt come from the lines and the `text` block under it. Edit them here, then run `install.sh` again. Times are in the profile's `timezone`, Asia/Saigon.

Both jobs deliver to `bot-chat:dispatcher`, the dispatcher's Bot Chat in Hermes Desktop. Hermes cron cannot target any other named Desktop session. A run that prints `[SILENT]` delivers nothing.

## dispatcher-drain

Schedule: `every 2h`
Deliver: `bot-chat:dispatcher`
Skill: `dispatch`

The `team-kit-github` webhook route fires this job for every event `github-triage.py` keeps, with the event's fields as context. The schedule is a safety net for events GitHub failed to deliver.

```text
Run the dispatcher-drain procedure of the dispatch skill (references/drain.md).
If this run carries a team-kit unit event, handle it first, then sweep every open unit.
If it carries none, only sweep.
Never run `team-kit-gh read` in this job.
Print [SILENT] unless a human gate opened. Otherwise print only the gate lines.
```

## dispatch-status

Schedule: `0 9,13,17 * * *`
Deliver: `bot-chat:dispatcher`
Skill: `dispatch`

```text
Write the dispatch status digest of the dispatch skill: exactly three lines,
In flight, Waiting on you, and Done since last. Use only kanban_list and kanban_show.
```
