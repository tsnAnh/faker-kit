# Using the team kit

The kit gives your coding agent the Cursor team's working methods (pstack and its companions) and a way to make the agent more trustworthy every time you correct it. It works in Claude Code, Codex, and Pi. Each person uses whichever one they already have.

## Pick your starting page

| You are | Start with | What you will do in the first hour |
|---|---|---|
| An engineer | [Engineers](start/engineers.md) | Install, set up a repository, build a verification skill, ship a proven change, encode your first correction |
| A designer | [Designers](start/designers.md) | Ask for two or three versions of a screen, compare screenshots, and hand the chosen one to an engineer as a pull request |
| In product or marketing | [Product and marketing](start/product-and-marketing.md) | Change copy in every language, report a bug so an agent reproduces it, and ask how a feature works |
| A founder or lead | [Founders and leads](start/founders-and-leads.md) | See what shipped, ask why things are built the way they are, and decide how many agents the team can trust |

Not sure? Install the kit ([how](how-to/install.md)) and type `/start-here`. It asks what you want to do in plain words and picks the right skill.

## The idea in one minute

An agent is only as trustworthy as the place it works. When an agent gets something wrong and you correct it in chat, that correction helps one agent, once. The kit's `correct` skill instead puts the fix where every future agent meets it, choosing the strongest place that can hold it:

1. **Codebase.** Make the mistake impossible to write: types, module boundaries, one blessed way to do each thing.
2. **Static analysis.** Make it fail CI: lint rules, compiler settings, checks.
3. **Rules and review bots.** Make it visible: AGENTS.md, plus Claude and Codex reviewing every pull request.
4. **Skills.** Teach the procedure, above all how to run the app and prove a change works.
5. **Style guide.** Write it down for human reviewers, until it can climb to a stronger layer.

As more corrections land in layers 1 and 2, the same mistake stops coming back, and you can run more agents at once without reading every line they write. [The trust ladder](explanation/trust-ladder.md) explains why.

## Everything else

**How-to guides** for a specific job:

- [Install the kit](how-to/install.md) in Claude Code, Codex, or Pi
- [Choose models](how-to/models.md)
- [Set up a repository](how-to/set-up-a-repo.md)
- [Correct an agent so it stays corrected](how-to/correct-an-agent.md)
- [Build a verification skill](how-to/build-a-verification-skill.md)
- [Design before building](how-to/design-before-building.md)
- [Build, review, and ship a change](how-to/build-review-ship.md)
- [Run many agents](how-to/run-many-agents.md)
- [Garden the codebase](how-to/garden-the-codebase.md)
- [Capture lessons from past sessions](how-to/capture-lessons.md)
- [Roll the kit out to a team](how-to/roll-out-to-a-team.md)

**Reference** to look things up:

- [Every skill](reference/skills.md), by layer, with who it is for and a prompt to try
- [Prompts](reference/prompts.md) to copy, by role
- [Playbooks and principles](reference/playbooks.md) that `/poteto-mode` uses
- [Lint packs](reference/lint-packs.md) for Kotlin/Android, KMP, Rust, Python, Flutter/Dart, Swift/iOS, and any stack
- [Models](reference/models.md): how upstream model names map to ours
- [Cursor terms in Claude Code, Codex, and Pi](../plugins/trust-ladder/references/compat.md)
- [Coverage](reference/coverage.md): where everything from the talk and posts lives

**Explanations** of why it works this way:

- [The trust ladder](explanation/trust-ladder.md)
- [Verification is all you need](explanation/verification-is-all-you-need.md)
- [Plan with code](explanation/plan-with-code.md)
- [Scaling to many agents](explanation/scaling-agents.md)
- [Why non-engineers can ship safely](explanation/why-non-engineers-can-ship-safely.md)

pstack also ships its own [guide](../plugins/pstack/docs/guide/README.md), written by its author. It is the best tour of `/poteto-mode` and its skills. Read it for the pstack parts; where it says Cursor, use the [compat table](../plugins/trust-ladder/references/compat.md).
