# Set up a repository

Do this once per repository. The result is one pull request that makes the repository safe for agents and usable by anyone on the team.

## Run it

On a new branch:

```text
/setup-faker-kit
```

The skill inspects the repository before it asks you anything. If the repository does not build or its tests fail as they are, it stops and tells you: setting up on a broken base teaches agents the wrong commands.

## What it adds

| Layer | Files | What they do |
|---|---|---|
| 3. Rules | `AGENTS.md`, `CLAUDE.md` (`@AGENTS.md`) | Commands, layout, boundaries, paved paths, a never-list, `## Review guidelines`, and the table that maps pstack's upstream terms to Claude Code |
| 5. Style guide | `REVIEW.md`, `STYLE.md` | What reviewers check, and the team's taste with bad and good examples |
| 3. Review bots | `.github/workflows/claude-review.yml`, `.agents/review-bot-patterns.md` | The Claude review of every push to a pull request, with its risk rating and security job, and the repository's learned triage patterns. No API keys |
| 4. Skills | `.claude/skills/`, `.agents/skills/` links | Project skills (such as your verification skill) visible to Claude Code |
| Team | `.claude/settings.json`, `CODEOWNERS` | The kit and safe defaults for every teammate's agent, and a named reviewer for every path |
| Measurement | The rule table in AGENTS.md, the `/check-agent-compatibility` report | Each rule and what enforces it, and the first readiness report |

It records the repository's own build, test, and lint commands in AGENTS.md and adds no check script or lint configuration. Its one CI addition is a step that runs `scripts/link-skills.sh --check .` and `.agents/faker-kit/vendor_kit.py --check .` on every pull request, in the repository's existing CI workflow or, when there is none besides the review workflow, in a small `faker-kit-checks.yml`. To stop a mistake agents keep making, run [`/correct`](correct-an-agent.md), which adds a targeted lint to the repository's own CI.

## After it merges

1. **Turn on the review.** Install the Claude GitHub app on the repository (github.com/apps/claude) and add a `CLAUDE_CODE_OAUTH_TOKEN` repository secret from `claude setup-token`. It draws on a teammate's plan, so the repository needs no API key. Merge the pull request that adds `claude-review.yml` yourself: the review does not run on a pull request that edits its own workflow. `/setup-faker-kit` (Review bots) walks through it.
2. **Branch protection.** Work through [the checklist](../../plugins/trust-ladder/templates/branch-protection.md): require the repository's CI checks, resolved review conversations, and a code owner's approval, with no bypass. This is what makes it safe for non-engineers to open pull requests.
3. **A verification skill.** Run `/create-verification-skill` next. It is the single most valuable skill a repository can have. See the pstack guide's [Verify the result and open a PR](../../plugins/pstack/docs/guide/06-verify-and-ship.md).
4. **Cloud sessions.** When you want agents to work in parallel without your laptop, run `/setup-cloud-agents`.

## Check the result

- The build, test, and lint commands in AGENTS.md pass on the current code.
- `/check-agent-compatibility` reports how ready the repository is for agents. Keep that first report; it is the baseline you improve on.
