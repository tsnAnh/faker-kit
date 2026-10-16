# Bootstrap bot scheduled jobs

`team-kit-admin add-bootstrap` creates these jobs, active, in the bootstrap bot's profile from this file, the same way `project-jobs.md` describes for project bots: each `##` heading is the job name, each job is a Hermes `--no-agent` job that runs its `Command` with the installed wrapper, and every delivered line starts with `team-kit notice:`. `hermes/install.sh` rewrites them.

## team-kit-inbox

Schedule: `every 30m`
Deliver: `bot-chat:{bot}`
Command: `team-kit-admin inbox`
Output: `print`

Delivers one waiting chief project request per run as a notice: `team-kit notice: chief project request <id> for OWNER/REPO ...`. `team-kit-chief new-project` starts this job at once, so the request arrives in about a minute; the schedule is the fallback. With `ask_owner: false` in `guard.yaml`, the guard tells the bootstrap bot to run `team-kit-admin setup-request --id <id>` in the notice turn; otherwise the owner says "set it up" in its Bot Chat.

Event coverage and the plan for this job's schedule: [Moving Hermes from polling to events](../../docs/explanation/event-driven-migration.md).
