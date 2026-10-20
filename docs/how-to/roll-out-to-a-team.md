# Roll the kit out to a team

A rollout that works in a week and keeps working after a year.

## Week 1: one repository, done properly

1. An engineer installs the kit ([Install](install.md)) and runs `/setup-faker-kit` on the main repository. Merge its pull request.
2. Turn on the Claude review (`/setup-faker-kit`, Review bots; no API keys) and [branch protection](../../plugins/trust-ladder/templates/branch-protection.md). From now on every change, from anyone or any agent, passes the same gates.
3. Run `/create-verification-skill` and merge it. Until this exists, agents cannot prove their work, so the rest of the team cannot rely on them.
4. Run `/setup-cloud-agents`. The repository then boots in a fresh cloud session with the kit loaded, and you have chosen how each surface shows screenshots on pull requests, including the mobile apps a cloud machine cannot run.
5. To have a bot run the work while you are away, connect the repository to Hermes dispatch: [Dispatch work with Hermes](dispatch-with-hermes.md).
6. Run `/check-agent-compatibility` and keep its report as the baseline.

## Week 2: everyone starts

Engineers install the kit once. The repository's `.claude/settings.json` makes it appear for Claude Code users after they trust the folder. Point each engineer to [Engineers](../start/engineers.md).

Designers, marketers, and founders install nothing. Give each one a seat on the team's Claude plan, have them connect GitHub, and have them open claude.ai/code and pick the repository. Suggest a first task that is small and visible, such as a copy change through `/faker-mode`, reviewed the same day. A fast first review matters more than the size of the change.

## Roles

| Role | Who | What they do |
|---|---|---|
| Verification-skill owner | One engineer, with an on-call rotation once many agents depend on it | Keeps the verification skill and its feature map working; reviews the `/maintain-verification-skill` pull request |
| Code owners | Per area, in CODEOWNERS | Review and merge pull requests, including those opened by non-engineers |
| Kit maintainer | One engineer | Updates the kit from upstream, adjusts models, and adds team skills to this repository |
| Review owner | One teammate with Claude Code | Owns the `CLAUDE_CODE_OAUTH_TOKEN` the review workflow draws on. When they leave, someone else runs `claude setup-token` and replaces the secret |

## Rituals

- **After every correction**: `/correct`, so the fix lands in a check, not a chat.
- **Weekly**: `/weekly-review`; a look at new rows in the AGENTS.md rule table.
- **Monthly**: `/check-agent-compatibility` and `/style-guide-from-reviews`. Decide whether to raise `max_in_flight` in `.agents/dispatch.json`.

## Numbers to watch

| Number | Where | Healthy direction |
|---|---|---|
| Share of rules a check enforces | The AGENTS.md rule table | Up |
| Rules nothing enforces | The AGENTS.md rule table | Zero |
| Agent-readiness report | `/check-agent-compatibility`, run monthly | Up, with its main problems fixed |
| Time for `scripts/check.sh` | Timed by hand on each change | Under 15 minutes |
| Ratchet counts | `.agents/ratchet-baseline.json` | Down |
| Pull requests merged with verification evidence | Pull request descriptions | Up, toward all of them |

## More repositories

Run `/setup-faker-kit` in each one. Repositories that share a stack share the same lint pack, so a rule encoded in one can be copied to the others; the corrections ledger shows which ones are worth copying.

## Keeping the kit current

The kit maintainer updates the vendored plugins with `node scripts/sync-upstream.mjs --ref <sha>` in this repository and reviews `upstream/CHANGES.md` in the diff. Claude Code users then run `claude plugin marketplace update faker-kit`. See this repository's [AGENTS.md](../../AGENTS.md).
