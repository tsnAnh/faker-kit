# Bootstrap bot scheduled jobs

`team-kit-admin add-bootstrap` creates these jobs, active, in the bootstrap bot's profile from this file, the same way `project-jobs.md` describes for project bots: each `##` heading is the job name, each job is a Hermes `--no-agent` job that runs its `Command` with the installed wrapper, and every delivered line starts with `team-kit notice:`. `hermes/install.sh` rewrites them.

## team-kit-inbox

Schedule: `every 30m`
Deliver: `bot-chat:{bot}`
Command: `team-kit-admin inbox`
Output: `print`

Delivers one waiting chief project request per run as a notice: `team-kit notice: chief project request <id> for OWNER/REPO ...`. `team-kit-chief new-project` starts this job at once, so the request arrives in about a minute; the schedule is the fallback. With `ask_owner: false` in `guard.yaml`, the guard tells the bootstrap bot to run `team-kit-admin setup-request --id <id>` in the notice turn; otherwise the owner says "set it up" in its Bot Chat.

## team-kit-kit-drift

Schedule: `every 30m`
Deliver: `bot-chat:{bot}`
Command: `team-kit-admin kit-reconcile`
Output: `print`

Reconciles every registered project with the kit installed on this machine, in code. For each project it reads three values on the default branch (this machine's kit commit, `kit_ref` in `.agents/dispatch.json`, and `kit_ref` in `.agents/team-kit/KIT.json`) and its open kit pull requests. A project that is behind the installed kit, or whose kit pull request is missing, for another commit, or conflicting, gets one `team-kit-admin vendor` run, never a second while that pull request is open, and is read back afterwards. Tick lands the pull request under the existing `auto_merge` rule. Only a state the owner must settle sends a notice, once per distinct state: `team-kit notice: kit drift in project <p> (OWNER/REPO) needs the owner: <why>. Installed kit <sha>, kit_ref <sha>, vendored kit <sha>; kit pull request <url>.` That covers this machine's kit older than `kit_ref`, a red kit pull request, a failed vendor run, a kit pull request whose head is not the commit vendor made, and a merge the policy leaves to the owner. It never installs a kit, merges, or changes `kit_ref` except through `vendor`, and holds while an andon cord is pulled. `team-kit-admin kit-drift` prints the same report without changing anything.
