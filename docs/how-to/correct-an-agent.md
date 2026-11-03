# Correct an agent so it stays corrected

When agents keep making the same mistake, fix the repository, not your next prompt. `/correct` is pstack's skill for this: it finds the mistakes agents repeat and makes each one impossible.

## When to run it

- You have corrected agents for the same mistake more than once.
- A reviewer flags the same thing on two pull requests.
- Someone says "don't do that again" or "can we encode this".

Only a person starts it; an agent does not run it on its own. A one-off misunderstanding of a single task does not need it.

## Run it

```text
/correct agents keep adding a new date helper instead of using DateFormatter
```

Run it with no argument and it finds the mistake classes from history on its own.

The pstack guide's [Fix the environment with `/correct`](../../plugins/pstack/docs/guide/09-make-it-yours.md#fix-the-environment-with-correct) explains the steps: it finds the mistake classes, fixes each at the highest level that works (architecture, then types or a lint or CI check, then a test, then docs), and proves each new check fails on a real past mistake.

In this kit it also keeps the `## Rules and what enforces them` table in AGENTS.md. The table pairs each rule with what enforces it. A rule that is already there with nothing enforcing it is a repeat, and gets fixed at a higher level in the same change. Between runs, agents do their part: when corrected, they fix the mistake and add the rule to the table, as the AGENTS.md template tells them.

Exceptions to a check go on the offending line with a reason, an expiry date, and a human's approval.

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
| "Error messages should sound calm" | A STYLE.md entry, then an AGENTS.md rule once the wording is settled |

[The trust ladder reference](../../plugins/trust-ladder/references/trust-ladder.md) has more routing examples.

## Stop a pattern from spreading before it is banned

Sometimes a pattern is bad but too widespread to fix in one change. `/correct` then adds a check to the repository's own CI that counts the pattern and fails only when a change adds more, with a message that names what to use instead, such as `Use DateFormatter from :core:time`. Each cleanup lowers the count; nobody can raise it.

## See the climb

Every few weeks, read the rule table in AGENTS.md. The share of rules a check enforces (architecture, types, lint, CI, or a test) should grow, and no rule should be left to the doc alone. A rule that keeps coming back means the mechanism did not hold; run `/correct` again.
