# Chief of staff scheduled jobs

`faker-kit install` creates these jobs in the chief of staff's profile from this file, the same way `project-jobs.md` gives each project bot its jobs: each `##` heading is the job name, and its schedule, delivery, skill, and prompt come from the lines under it. `{bot}` becomes the chief's profile name. The schedules are cron expressions in the profile's `timezone`, Asia/Ho_Chi_Minh, and run on weekdays only. Edit them here, then run `hermes/install.sh` again; it rewrites each job's schedule and prompt in place.

Both are agent jobs: a model turn in the chief's profile with the `chief` skill loaded. The loop job carries a Script gate, `faker-kit-loop-gate.py`, which `install.sh` writes into the chief's `scripts/` folder beside `faker_kit_gate.py` and `faker_kit_cron_list.py`. It runs `faker-kit loop --all` and wakes the agent only when a `waiting` part is due (its provider's pull request belongs to a `merged` part of the same project) or `to_report` lists a part; otherwise it prints `{"wakeAgent": false}` and the tick costs no turn. It keeps no state: a failing command, an unexpected shape, or a timeout exits non-zero, Hermes shows a Script Error, and the agent runs the loop pass itself. The digest job has no gate. The chief owns the loop for every part it handed out, hears from project bots when a part changes instead of polling them, and stays quiet while that work is healthy: it speaks only when a part is blocked, ready for the owner to merge, or done, and batches everything else into the morning digest.

## faker-kit-loop

Schedule: `0 10-18 * * 1-5`
Deliver: `bot-chat:{bot}`
Skill: `chief`
Script: `faker-kit-loop-gate.py`
Prompt: `Run the loop. Follow the Each loop pass section of the chief skill. If a Script Error block is shown, the gate could not read the loop: run faker-kit loop and faker-kit status --all yourself and go on. Otherwise the Script Output above lists the due waiting parts and the to_report parts, so run faker-kit status --all and not faker-kit loop again, hand each due waiting part to its consumer bot, and message no other bot: project bots report changes themselves. Tell this chat only the parts faker-kit loop lists under to_report, one line each with the pull request link, then mark each with faker-kit loop set <id> --reported. Answer [SILENT] when to_report is empty.`

## faker-kit-digest

Schedule: `0 9 * * 1-5`
Deliver: `bot-chat:{bot}`
Skill: `chief`
Prompt: `Write the morning digest. Follow the Digest section of the chief skill: run faker-kit loop digest and faker-kit status --all, then send one short message: any andon cord, what closed since the last digest, what is in flight and with which bot, and what waits on the owner. Then ask the bot of each handed-out or in-progress part with no news for a day one plain question about it. Answer [SILENT] when closed, in_flight, and waiting_on_owner are all empty and no cord is pulled.`
