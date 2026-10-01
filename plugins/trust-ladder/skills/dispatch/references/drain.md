# The drain job

`dispatcher-drain` is a cron job in the `dispatcher` profile. The webhook route `team-kit-github` fires it after `github-triage.py` keeps an event. Its prompt carries one event's fields: `event`, `repo`, `card`, `issue`, `pr`, `status`, `conclusion`. A field the event does not have is `-`.

Hermes runs the job at most once at a time, so events that arrive during a run can be folded into it. Handle the event in the prompt, then sweep every open unit, so a missed event is caught on the next run.

Approvals are denied in cron runs. Never run `team-kit-gh read` here. It taints the turn, and every later side-effecting call is then denied. Read only `team-kit-gh report-status`, `kanban_list`, and `kanban_show`.

## Check the event belongs to a unit

`github-triage.py` keeps only events by a login in guard.yaml `trusted_authors`, and only pull requests from a branch in the same repository. Still, before acting, `kanban_show(<card>)` and check:

- The card exists, its assignee is `launcher`, and its `issue:` comment names the event's `repo` and `issue`.

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
| `pr_merged` | `kanban_comment(<card>, "merged: <pr url>")`. If the card is not done, `kanban_complete` it as for `checks_finished`. Then open the oldest queued unit (below). |

## Open a gate

1. Run `team-kit-gh report-status --repo <repo> --issue <issue>`. If the latest status is now `done`, stop. The agent recovered.
2. If the card already has a `needs owner:` comment newer than its `launch:` comment, stop. The gate is open.
3. Run `team-kit-gh needs-human --repo <repo> --issue <issue>`.
4. `kanban_comment(<card>, "needs owner: report <status> on <issue url>")`.
5. If the card is not blocked, `kanban_block(<card>, reason="needs owner: report <status>")`.
6. Add one line to the output for the owner: `<card> needs you: the agent reported <status> on <issue url>.`

## Sweep

After the event, for each card assigned to `launcher` that is not done and has an `issue:` comment:

1. Run `team-kit-gh report-status` for its issue. If the status is `partial`, `blocked`, or `unreadable`, open a gate.
2. If the card is blocked and a pull request is known (its `pr:` comment, or the `pr` field of the report), try `kanban_complete` as for `checks_finished`. A refusal is expected.

Then, if any card has a `queued: max_in_flight` comment and no `issue:` comment, open the oldest one with steps 4 and 5 of the skill. Stop at the first `max_in_flight` refusal.

## Output

Print `[SILENT]` unless a gate opened. Otherwise print only the gate lines, one per gate. Hermes delivers them to the owner's Desktop Bot Chat.
