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
| 3. Rules | `AGENTS.md`, `CLAUDE.md` (`@AGENTS.md`) | Commands, layout, boundaries, paved paths, a never-list, and the table that maps pstack's upstream terms to Claude Code |
| 5. Style guide | `STYLE.md` | The team's taste, with bad and good examples |
| 4. Skills | `.claude/skills/`, `.agents/skills/` links | Project skills (such as your verification skill) visible to Claude Code |
| Team | `.claude/settings.json`, `CODEOWNERS` | The kit and safe defaults for every teammate's agent, and a named reviewer for every path |
| Measurement | The rule table in AGENTS.md, the `/check-agent-compatibility` report | Each rule and what enforces it, and the first readiness report |

It records the repository's own build, test, and lint commands in AGENTS.md and adds no check script or lint configuration. Its one CI addition is a step that runs `scripts/link-skills.sh --check .` and `.agents/faker-kit/vendor_kit.py --check .` on every pull request, in the repository's existing CI workflow or, when there is none, in a small `faker-kit-checks.yml`. To stop a mistake agents keep making, run [`/correct`](correct-an-agent.md), which adds a targeted lint to the repository's own CI.

## After it merges

1. **CI.** Make sure a GitHub Actions workflow builds and tests every pull request. A pull request is checked by it, and a Hermes project bot lands only pull requests with a passing run from it.
2. **Branch protection.** Work through [the checklist](../../plugins/trust-ladder/templates/branch-protection.md): require the repository's CI checks, resolved review conversations, and a code owner's approval, with no bypass. This is what makes it safe for non-engineers to open pull requests.
3. **Verification skills.** Run `/create-verification-skill` next. It is the single most valuable skill a repository can have. See the pstack guide's [Verify the result and open a PR](../../plugins/pstack/docs/guide/06-verify-and-ship.md). Where a missed case is costly, such as a parser or a permission check, also run `/create-formal-verification-skill`. Before a pull request merges, a separate verifier session runs these skills on the exact head and comments a verdict.
4. **Protected paths.** List the paths only you merge under `risk_paths_high` in `.agents/dispatch.json` (`/setup-hermes-project` does this), and name their owners in `CODEOWNERS`.
5. **Cloud sessions.** When you want agents to work in parallel without your laptop, run `/setup-cloud-agents`.

## Check the result

- The build, test, and lint commands in AGENTS.md pass on the current code.
- `/check-agent-compatibility` reports how ready the repository is for agents. Keep that first report; it is the baseline you improve on.
