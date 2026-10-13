# Roll the kit out to a team

A rollout that works in a week and keeps working after a year.

## Week 1: one repository, done properly

1. An engineer installs the kit ([Install](install.md)) and runs `/setup-team-kit` on the main repository. Merge its pull request.
2. Turn on the review bots (`/setup-review-bots`; no API keys) and [branch protection](../../plugins/trust-ladder/templates/branch-protection.md). From now on every change, from anyone or any agent, passes the same gates.
3. Run `/create-verification-skill` and merge it. Until this exists, agents cannot prove their work, so the rest of the team cannot rely on them.
4. Run `/setup-cloud-agents`. The repository then boots in a fresh cloud session with the kit loaded, and you have chosen how each surface shows screenshots on pull requests, including the mobile apps a cloud machine cannot run.
5. Run `/setup-routines` and keep at least the requested-changes job and its issue form.
6. Run `/trust-audit` and keep the first row as the baseline. Its non-engineer check should be all yes before week 2.

## Week 2: everyone starts

Engineers install the kit once, in the one agent they use. The repository's settings files make it appear for Claude Code, Codex, and Pi users after they trust the folder.

Designers, marketers, and founders install nothing. Give each one a seat on the team's Claude or ChatGPT plan (whichever agent the team's cloud environment is set up in), have them connect GitHub, and send them [Use the kit from a browser or your phone](use-the-kit-from-the-browser.md).

Point each person to their start page:

- [Engineers](../start/engineers.md)
- [Designers](../start/designers.md)
- [Product and marketing](../start/product-and-marketing.md)
- [Founders and leads](../start/founders-and-leads.md)

Suggest a first task for non-engineers that is small and visible, such as a copy change through `/request-a-change`, reviewed the same day. A fast first review matters more than the size of the change.

## Roles

| Role | Who | What they do |
|---|---|---|
| Gardener | One engineer, rotating monthly | Reads the gardener routine's report, decides which smells to stop, and runs `/correct` or `/paved-path` on them |
| Verification-skill owner | One engineer, with an on-call rotation once many agents depend on it | Keeps the verification skill and its feature map working; reviews the nightly `/maintain-verification-skill` pull request |
| Code owners | Per area, in CODEOWNERS | Review and merge pull requests, including those opened by non-engineers and routines |
| Kit maintainer | One engineer | Updates the kit from upstream, adjusts models, and adds team skills to this repository |
| Bot owners | One teammate with Claude Code, one with Codex | Run the Claude review routine and the other routines on their plan, and connect Codex code review. When one leaves, someone else recreates their routines from `.agents/routines/` |

## Rituals

- **Daily, automated**: the verification-skill and gardener routines ([Run many agents](run-many-agents.md#routines)).
- **After every correction**: `/correct`, so the fix lands in a check, not a chat.
- **Weekly**: the weekly review issue; the gardener's top three; a look at new rows in the AGENTS.md rule table.
- **Monthly**: `/trust-audit` and `/style-guide-from-reviews`. Decide whether to raise the number of agents you run at once.

## Numbers to watch

| Number | Where | Healthy direction |
|---|---|---|
| Share of rules a check enforces | The AGENTS.md rule table | Up |
| Rules nothing enforces | The AGENTS.md rule table | Zero |
| Trust score per layer | `.agents/trust-audit.tsv` | Up, with the lowest layer rising |
| Time for `scripts/check.sh` | `.agents/trust-audit.tsv` | Under 15 minutes |
| Ratchet counts | `.agents/ratchet-baseline.json` | Down |
| Pull requests merged with verification evidence | Pull request descriptions | Up, toward all of them |
| Requested changes from non-engineers, and days from issue to merge | Issues and pull requests labeled `requested-change` | Count up, days down |

## More repositories

Run `/setup-team-kit` in each one. Repositories that share a stack share the same lint pack, so a rule encoded in one can be copied to the others; the corrections ledger shows which ones are worth copying.

## Keeping the kit current

The kit maintainer updates the vendored plugins with `node scripts/sync-upstream.mjs --ref <sha>` in this repository and reviews `upstream/CHANGES.md` in the diff. Claude Code users then run `claude plugin marketplace update faker-kit`; Codex users run `codex plugin marketplace upgrade`; Pi teams bump the pinned commit in `.pi/settings.json`. See this repository's [AGENTS.md](../../AGENTS.md).
