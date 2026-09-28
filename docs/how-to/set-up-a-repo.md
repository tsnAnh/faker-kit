# Set up a repository

Do this once per repository. The result is one pull request that makes the repository safe for agents and usable by anyone on the team.

## Run it

On a new branch:

```text
/setup-team-kit
```

The skill inspects the repository before it asks you anything. If the repository does not build or its tests fail as they are, it stops and tells you: setting up on a broken base teaches agents the wrong commands.

## What it adds

| Layer | Files | What they do |
|---|---|---|
| 3. Rules | `AGENTS.md`, `CLAUDE.md` (`@AGENTS.md`) | Commands, layout, boundaries, paved paths, a never-list, `## Review guidelines`, and the table that maps pstack's upstream terms to your agent |
| 5. Style guide | `REVIEW.md`, `STYLE.md` | What reviewers check, and the team's taste with bad and good examples |
| 2. Static analysis | `scripts/check.sh`, lint configs, baselines, a CI job | One command that runs everything CI runs. Existing violations are recorded in a baseline so only new ones fail |
| 3. Review bots | `.agents/routines/pr-review.md` | The Claude reviewer's instructions. With Codex code review, Claude and Codex review every pull request, with no API keys |
| 4. Skills | `.claude/skills/`, `.agents/skills/` links | Project skills (such as your verification skill) visible to Claude Code, Codex, and Pi |
| Team | `.claude/settings.json`, `.codex/config.toml`, `.pi/settings.json`, `CODEOWNERS` | The kit and safe defaults for every teammate's agent, and a named reviewer for every path |
| Measurement | `.agents/corrections.tsv`, `.agents/trust-audit.tsv` | The ledger of encoded corrections and the first trust score |

It installs one [lint pack](../reference/lint-packs.md) per detected stack, plus the agnostic pack every repository gets. To add a pack later: `/setup-lint-pack rust`.

## After it merges

1. **Turn on the review bots.** They run on teammates' subscriptions, so the repository needs no API-key secrets. Someone with Codex turns on Codex code review for the repository in Codex settings. Someone with Claude Code creates a routine at claude.ai/code/routines that follows `.agents/routines/pr-review.md` whenever a pull request opens. `setup-review-bots` walks through both.
2. **Branch protection.** Work through [the checklist](../../plugins/trust-ladder/templates/branch-protection.md): require the check job, resolved review conversations, and a code owner's approval, with no bypass. This is what makes it safe for non-engineers to open pull requests.
3. **A verification skill.** Run `/create-verification-skill` next. It is the single most valuable skill a repository can have. See [Build a verification skill](build-a-verification-skill.md).
4. **Cloud sessions.** When you want agents to work in parallel without your laptop, run `/setup-cloud-agents`.

## Check the result

- `scripts/check.sh` passes on the current code.
- Breaking one rule on purpose (for example, adding a `print` in Swift or an `unwrap()` in Rust) makes it fail with a message that says what to do instead.
- `/trust-audit` gives a score per layer. Keep that first row; it is the baseline you improve on.
