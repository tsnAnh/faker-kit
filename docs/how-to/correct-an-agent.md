# Correct an agent so it stays corrected

When agents keep making the same mistake, fix the repository, not your next prompt. `/correct` is pstack's skill for this: it finds the mistakes agents repeat and makes each one impossible.

## When to run it

- You have corrected agents for the same mistake more than once.
- A reviewer, or a review bot, flags the same thing on two pull requests.
- Someone says "don't do that again" or "can we encode this".

Only a person starts it; an agent does not run it on its own. A one-off misunderstanding of a single task does not need it.

## Run it

```text
/correct agents keep adding a new date helper instead of using DateFormatter
```

Run it with no argument and it finds the mistake classes from history on its own.

It then:

1. **Finds the mistake classes:** it reads recent commits, reverts, review comments, agent instruction files, and comments that explain workarounds, and groups the mistakes. A class counts once it has happened twice.
2. **Fixes each class at the highest level that works:**
   1. **Architecture:** one owner for each piece of state and one supported way to do each task; internals hidden so the wrong import fails; old ways an agent would copy deleted. Use `paved-path` when callers must move.
   2. **Types**, so the bad state cannot be written. Where bad code still compiles, a **lint or CI check** whose error names what to use instead (`encode-lint-rule`). If the pattern is already common, it fails only when a change adds more.
   3. **A test** of the behavior.
   4. **Docs or agent rules last**, only for judgment calls: nothing fails when an agent skips them. Human review is not a level.
3. **Proves it:** each new check fails on a real past mistake, and runs the same way locally and in CI. Exceptions go on the offending line with a reason, an expiry date, and a human's approval.
4. **Keeps the rule table:** the `## Rules and what enforces them` table in AGENTS.md pairs each rule with what enforces it. A rule that is already there with nothing enforcing it is a repeat, and gets fixed at a higher level in the same change.

It replies with each class, its evidence, the level it picked, and why a higher level did not work. Between runs, agents do their part: when corrected, they fix the mistake and add the rule to the table, as the AGENTS.md template tells them.

## Examples by stack

| Correction | Where it lands |
|---|---|
| Kotlin: "stop using `!!`" | detekt `UnsafeCallOnNullableType` as an error (already in the kotlin-android pack) |
| Kotlin: "use `AppLog`, not `android.util.Log`" | detekt `ForbiddenImport` with a message naming `AppLog` |
| KMP: "no Android imports in commonMain" | The kmp pack's commonMain import rule, then expect/actual declarations |
| Rust: "no `unwrap()` outside tests" | `clippy::unwrap_used = "deny"` (rust pack) |
| Python: "UI layer must not import the database" | An import-linter layers contract |
| Swift: "no `print` for logging" | The swift-ios pack's ast-grep rule `swift-no-print` |
| Dart: "don't fire and forget futures" | `discarded_futures` as an error (flutter-dart pack) |
| Any stack: "stop explaining workarounds in comments" | The agnostic pack's `no-new-comments` check |
| Any stack: "every string needs all languages" | The agnostic pack's `locale-keys` check |
| "Error messages should sound calm" | A STYLE.md entry, then a review guideline once the wording is settled |

[The trust ladder reference](../../plugins/trust-ladder/references/trust-ladder.md) has more routing examples.

## Stop a pattern from spreading before it is banned

Sometimes a pattern is bad but too widespread to fix in one change. The agnostic pack's ratchet counts it and fails CI only when the count goes up:

```json
{ "id": "no-new-date-helpers", "pattern": "fun format\\w*Date\\(", "files": ["*.kt"], "message": "Use DateFormatter from :core:time." }
```

Add the rule to `.agents/ratchet.json`, then run `node scripts/agent-checks/ratchet.mjs update` to record today's count. Each cleanup lowers the count; nobody can raise it.

## See the climb

Every few weeks, read the rule table in AGENTS.md. The share of rules a check enforces (architecture, types, lint, CI, or a test) should grow, and no rule should be left to the doc alone. A rule that keeps coming back means the mechanism did not hold; run `/correct` again.
