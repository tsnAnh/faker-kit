# Correct an agent so it stays corrected

When an agent does something you do not want repeated, fix the environment, not the agent. `/correct` turns the correction into a mechanism every future agent meets.

## When to run it

- Right after you correct an agent in chat, if the mistake could happen again.
- When a reviewer flags the same thing twice.
- When someone says "don't do that again" or "can we encode this".

A one-off misunderstanding of a single task does not need it. `/correct` asks whether the mistake recurs, and stops if it does not.

## Run it

```text
/correct agents keep adding a new date helper instead of using DateFormatter
```

Give it the evidence if you have it: a pull request, a review comment, or the chat.

It then:

1. Writes a **bad example**, a **good example**, and the **rule** in one sentence.
2. Tries each layer, strongest first, and takes the first that can hold the rule:
   1. **Codebase:** make the bad code impossible to write (a type, one blessed API with the alternatives deleted, a boundary the build enforces). Uses `paved-path` when callers must move.
   2. **Static analysis:** make it fail CI, with a message that says what to do instead. Uses `encode-lint-rule`.
   3. **Rules and review bots:** a line in the right AGENTS.md, repeated under `## Review guidelines` so both review bots flag it.
   4. **Skills:** a step in the skill that owns the procedure, often the verification skill.
   5. **Style guide:** a STYLE.md entry naming the layer it should reach next.
3. **Proves it:** the check fails on the bad example and passes on the good one, and both are committed as fixtures. Existing violations are fixed, or recorded in a baseline that only goes down.
4. **Logs it:** one row in `.agents/corrections.tsv`.
5. Opens a small pull request titled `chore(agents): encode <rule>`.

It writes the rule once, at the strongest layer. A lint rule plus an AGENTS.md line saying the same thing is noise.

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

Every few weeks, read `.agents/corrections.tsv`. The share of rows in layers 1 and 2 should grow, and the same rule should not appear twice. If it does, the mechanism did not hold; run `/correct` again on the repeat.
