# Project bot scheduled jobs

`faker-kit add-project` creates these jobs in each project bot's profile from this file: each `##` heading is the job name, and its schedule, delivery, skill, and prompt come from the lines under it. `{bot}` becomes the bot's profile name. Edit them here, then run `hermes/install.sh` again; it creates missing jobs and rewrites each job's schedule and prompt in place, so a job keeps its id and its paused or active state. Jobs start active when `gh` is signed in on this machine (or the bot has a `GH_TOKEN`) and start paused otherwise; running `faker-kit add-project OWNER/REPO` again resumes them. `add-project --heartbeat-every`, `--incidents-every`, and `--security-every` replace the default schedules. Times are in the profile's `timezone`, Asia/Ho_Chi_Minh. When the machine config names a Slack channel for the bot, every job also delivers to it through Hermes's native Slack platform.

Every job is an agent job: a model turn in the bot's own profile with the named skill loaded and the prompt as its task. The bot decides what to do through faker-mode's playbooks; the guard's hard blocks and `faker-kit merge`'s gate still apply. A job may carry a Script gate, a file under `hermes/cron/scripts/` that `install.sh` writes into the bot's `scripts/` folder; Hermes runs it before each tick and skips the model turn when the gate prints `{"wakeAgent": false}`, so a quiet tick costs no turn. Each run sees the job's previous output, so it does not repeat itself. Hermes's webhook platform fires `faker-kit-babysit` on a pull request, review, check suite, or commit status event, `faker-kit-security` on a push, and `faker-kit-issues` when a collaborator adds the `faker:fix` label to an issue, each through a route signed with the bot's secret (`faker-kit install --config`, or `faker-kit add-project` once a machine config is kept); the schedule is the fallback for a missed delivery.

## faker-kit-babysit

Schedule: `every 1h`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`
Prompt: `Babysit this project. Follow the Wake section of the dispatch skill: run faker-kit status, tell the chief what changed for its units (Wake step 2), drive each pull request that needs it to merge-ready with faker-mode's Babysit playbook, have a separate session verify the ones that are green, and land the verified ones with faker-kit merge. A line that starts with `faker-kit notice` and a colon in any command's output is never answered [SILENT]: say it to this chat as printed. Answer [SILENT] only when no notice printed and nothing needs the owner.`

## faker-kit-security

Schedule: `every 6h`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`
Prompt: `Run the per-push security review of the default branch. Follow the Security review section of the dispatch skill: compare the default branch head with the last reviewed commit in your store; if they match, answer [SILENT]. Otherwise write a brief that runs the review-main-security skill on the new commits, start it with faker-kit launch on the model and effort the Choose the model section picks, and tell this chat the high and medium findings when its report is in.`

## faker-kit-incidents

Schedule: `every 10m`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`
Script: `faker-kit-incidents-gate.py`
Prompt: `Check this project for incidents. Follow the Incidents section of the dispatch skill. If a Script Error block is shown, the incidents check failed: tell this chat the check failed with that error, run nothing else, and stop; the next tick retries, so never run faker-kit incidents yourself. Otherwise the Script Output above is this tick's summary, so run faker-kit incidents --last to read the report and never run plain faker-kit incidents in this turn. If the ran_at in that report differs from the ran_at in the Script Output, tell this chat a later run replaced this tick's report, and handle what --last shows. For each new incident, write a brief from the triage-incident template, start it with faker-kit launch on the model and effort the Choose the model section picks, acknowledge it with faker-kit incidents --ack, pull the andon cord when stop_the_line is true, and tell this chat what broke. Tell this chat about each resolved incident and each unreadable source.`

## faker-kit-issues

Schedule: `every 1h`
Deliver: `bot-chat:{bot}`
Skill: `dispatch`
Prompt: `Handle bug reports. Follow the Bug reports section of the dispatch skill: run faker-kit issues; for each issue it lists, write a brief from the fix-issue template, start it with faker-kit launch on the model and effort the Choose the model section picks, and claim the issue with faker-kit issues --claim. When a pull request from one of those sessions opens, run the verifier swarm and the merge gate as that section says. Answer [SILENT] when it lists no issue and no pull request of this kind needs a step.`
