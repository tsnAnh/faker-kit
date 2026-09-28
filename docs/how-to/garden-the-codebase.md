# Garden the codebase

Agents copy what they see. One workaround in the codebase becomes ten within a week, because every agent that reads it treats it as the local convention. Gardening is the habit of catching those patterns while they are small.

## The weekly sweep

```text
/gardener what spread in the codebase this week?
```

It scans the last week of merges for the smells agents copy: a third copy of the same helper, lint suppressions creeping in, comments explaining a workaround, and escape hatches such as `any`, `!!`, `unwrap()`, and force casts. It ranks them by how fast they are spreading, not by how many exist. For the top three it:

1. Stops the bleeding: runs `/correct` to add a rule, usually a lint rule or a ratchet that blocks new instances without forcing a big cleanup today.
2. Files the cleanup as an issue, so an agent can take it later.

Run it daily or weekly as a routine ([Run many agents](run-many-agents.md#routines)). Give one person the gardener role: they read the report and decide what to act on.

## One blessed way per concern

When the gardener finds two ways of doing the same thing (two HTTP clients, three date helpers, two navigation patterns), make one the paved path:

```text
/paved-path we have three ways to format dates
```

It picks the path, migrates every caller, deletes the alternatives, and bans them with a lint message that names the path. This is what Cursor did with Dune, the framework that made the right way the only way. [Agent-friendly architecture](../../plugins/trust-ladder/references/agent-friendly-architecture.md) covers the patterns: one blessed API per concern, boundaries the build enforces, types that make bad states unrepresentable.

pstack's principle behind this: [migrate callers, then delete legacy APIs](../../plugins/pstack/skills/principle-migrate-callers-then-delete-legacy-apis/SKILL.md) in the same change, so nothing is left half-migrated for the next agent to copy.

## Comments are a smell here

Agents leave comments that justify a workaround ("retry here because the API is flaky") instead of fixing the cause, and the next agent reads the comment as permission. The kit attacks this three ways:

- `/no-comments` runs the Comment Sicko agent over a change before review.
- The agnostic pack's optional `no-new-comments` check fails CI on comments a change adds (turn it on with `/setup-lint-pack agnostic --with no-new-comments`). License headers, public API doc comments, lint suppressions, and lines that link an issue are allowed.
- AGENTS.md's never-list says: fix the root cause, or open a task and link it.

## Language habits

The `kotlin-best-practices`, `swift-best-practices`, `rust-best-practices`, `python-best-practices`, and `dart-best-practices` skills, like pstack's `typescript-best-practices`, show agents the idioms that keep code safe to copy. Their rules that a linter can check are already in the [lint packs](../reference/lint-packs.md).
