# The drain job

`team-kit-drain` is a cron job in your profile. Your webhook route `team-kit-<project>` fires it after `github-triage.py` keeps an event from one of your repositories. Its prompt carries one event's fields: `event`, `repo`, `card`, `issue`, `pr`, `status`, `conclusion`. A field the event does not have is `-`.

Hermes runs the job at most once at a time. An event that arrives while it runs is dropped, not queued. So handle the event in the prompt, sweep every open unit, then launch queued units. The sweep reads current state from GitHub and Kanban, so it catches what a dropped event would have done. The job's schedule runs the sweep even when no event arrives.

Approvals are denied in cron runs. Never run `team-kit-gh read` here. It taints the turn, and every later side-effecting call is then denied. Read only `team-kit-gh report-status`, `team-kit-gh pr-state`, `kanban_list`, and `kanban_show`.

Start every run with `team-kit-gh project`. Besides your bot name, it records how many of your units are running, which the other project bots on this machine read for the machine-wide limit.

## Check the event belongs to a unit

`github-triage.py` keeps only events from your repositories, by a login in guard.yaml `trusted_authors`, and only pull requests from a branch in the same repository. Still, before acting, `kanban_show(<card>)` and check:

- The card exists, its assignee is your bot, and its `issue:` comment names the event's `repo` and `issue`.

If any check fails, ignore the event.

## Per event

`<pr url>` is `https://github.com/<repo>/pull/<pr>`. `<issue url>` is the card's `issue:` comment.

| Event | Do |
|---|---|
| `pr_opened` | If the card is not done, `kanban_comment(<card>, "pr: <pr url>")`. |
| `checks_finished`, `conclusion` is `success` | If the card is not done, `kanban_complete(<card>, summary="Pull request <pr url> passed its required checks.", metadata={"published_pr": "<pr url>"})`. Kanban's completion contract refuses while any required check on the PR head is pending or failed. A refusal is expected. Leave the card, and a later event retries. |
| `checks_finished`, any other `conclusion` | Run `team-kit-gh report-status`. If the latest status is `done`, the agent finished but checks fail, so open a gate with the reason "checks <conclusion> after a done report". Otherwise leave the card. The agent is still working. |
| `report`, `status` is `done` | Run `team-kit-gh report-status --repo <repo> --issue <issue>`. Nothing else. The card completes when checks pass. |
| `report`, `status` is `partial`, `blocked`, or `unreadable` | Open a gate (below). |
| `pr_merged` | Run `team-kit-gh pr-state --repo <repo> --pr <pr>`. If `merged` is false, ignore the event. Otherwise `kanban_comment(<card>, "merged: <pr url>")`. If the card is not done, `kanban_complete` it as for `checks_finished`. The merge closed the issue, so launch queued units (below). |

## Open a gate

1. Run `team-kit-gh report-status --repo <repo> --issue <issue>`. If the latest status is now `done`, stop. The agent recovered.
2. If the card already has a `needs owner:` comment newer than its `launched:` comment, stop. The gate is open.
3. Run `team-kit-gh needs-human --repo <repo> --issue <issue>`. It also removes `agent:running`, because the agent has stopped, so queued units can use the slot.
4. `kanban_comment(<card>, "needs owner: report <status> on <issue url>")`.
5. Add one line to the output for the owner: `team-kit notice: <repo name>: <card> needs you. The agent reported <status> on <issue url>.`

## Sweep

After the event, for each card assigned to you that is not done and has a `launched:` comment:

1. Run `team-kit-gh report-status` for its issue. If the status is `partial`, `blocked`, or `unreadable`, open a gate.
2. If a pull request is known (its `pr:` comment, or the `pr` field of the report) and the card has no `merged:` comment, run `team-kit-gh pr-state --repo <repo> --pr <pr>`. If `merged` is true, do the `pr_merged` row.
3. Otherwise, if a pull request is known, try `kanban_complete` as for `checks_finished`. A refusal is expected.

## Launch queued units

Then, for each card assigned to you that has an `issue:` comment, no `launched:` comment, no `needs owner: launch` comment, and is not done, oldest first:

```text
team-kit-gh launch --repo <repo> --issue <issue> --size <size from the card body>
```

- On success, `kanban_comment(<card>, "launched: <session>")` and `kanban_block(<card>, reason="waiting for PR")`.
- On `waiting for dependencies`, leave the card and go on to the next one. Another unit may be ready.
- On `repo limit:`, skip the other queued cards in the same repository and go on.
- On `machine limit:`, stop. No slot is free on this machine.
- On any other refusal, open a launch gate (below) and go on.

## Open a launch gate

A launch gate has no report, so it skips the report steps.

1. If the card already has a `needs owner: launch` comment, stop. The gate is open.
2. `kanban_comment(<card>, "needs owner: launch refused: <the refusal's reason>")`.
3. Add one line to the output: `team-kit notice: <repo name>: <card> could not launch: <the refusal's reason>.`

The drain never retries a card with an open launch gate. After the owner fixes the cause, they ask you in Bot Chat to launch it, and you run step 5 of the skill.

Launching is not a gate. It prints nothing.

## Output

Print `[SILENT]` unless a gate opened. Otherwise print only the gate lines, one per gate, each starting with `team-kit notice:`. Hermes delivers them to your Bot Chat as a new turn. The guard taints any turn that contains `team-kit notice:`, so you cannot act on it without the owner's approval.
