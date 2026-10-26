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
   1. **Architecture:** one owner for each piece of state and one supported way to do each task; internals hidden so the wrong import fails; old ways an agent would copy deleted. When callers must move, migrate them and delete the old API in the same change.
   2. **Types**, so the bad state cannot be written. Where bad code still compiles, a **lint or CI check** whose error names what to use instead (written in the repository's own linter). If the pattern is already common, it fails only when a change adds more.
   3. **A test** of the behavior.
   4. **Docs or agent rules last**, only for judgment calls: nothing fails when an agent skips them. Human review is not a level.
3. **Proves it:** each new check fails on a real past mistake, and runs the same way locally and in CI. Exceptions go on the offending line with a reason, an expiry date, and a human's approval.
4. **Keeps the rule table:** the `## Rules and what enforces them` table in AGENTS.md pairs each rule with what enforces it. A rule that is already there with nothing enforcing it is a repeat, and gets fixed at a higher level in the same change.

It replies with each class, its evidence, the level it picked, and why a higher level did not work. Between runs, agents do their part: when corrected, they fix the mistake and add the rule to the table, as the AGENTS.md template tells them.

## Examples by stack

| Correction | Where it lands |
|---|---|
| Kotlin: "stop using `!!`" | detekt `UnsafeCallOnNullableType` as an error |
| Kotlin: "use `AppLog`, not `android.util.Log`" | detekt `ForbiddenImport` with a message naming `AppLog` |
| KMP: "no Android imports in commonMain" | A forbidden-import rule on `commonMain`, then expect/actual declarations |
| Rust: "no `unwrap()` outside tests" | `clippy::unwrap_used = "deny"` |
| Python: "UI layer must not import the database" | An import-linter layers contract |
| Swift: "no `print` for logging" | A SwiftLint custom rule that bans `print` |
| Dart: "don't fire and forget futures" | `discarded_futures` as an error in `analysis_options.yaml` |
| Any stack: "stop explaining workarounds in comments" | A CI check that fails when a change adds a comment; `/no-comments` clears the existing ones |
| Any stack: "every string needs all languages" | A CI check that compares every locale file's keys with the template locale |
| "Error messages should sound calm" | A STYLE.md entry, then a review guideline once the wording is settled |

[The trust ladder reference](../../plugins/trust-ladder/references/trust-ladder.md) has more routing examples.

## Stop a pattern from spreading before it is banned

Sometimes a pattern is bad but too widespread to fix in one change. `/correct` then adds a check to the repository's own CI that counts the pattern and fails only when a change adds more, with a message that names what to use instead, such as `Use DateFormatter from :core:time`. Each cleanup lowers the count; nobody can raise it.

## See the climb

Every few weeks, read the rule table in AGENTS.md. The share of rules a check enforces (architecture, types, lint, CI, or a test) should grow, and no rule should be left to the doc alone. A rule that keeps coming back means the mechanism did not hold; run `/correct` again.
