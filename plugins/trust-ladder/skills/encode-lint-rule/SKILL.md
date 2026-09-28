---
name: encode-lint-rule
description: Write a lint rule, compiler setting, or CI check that fails on a specific agent mistake and tells the next agent what to do instead, in the repository's own linter (detekt, ktlint, Android Lint, SwiftLint, ast-grep, clippy, ruff, mypy, import-linter, Dart analyzer) or the agnostic checks. Use when correct or paved-path picks layer 2, or for /encode-lint-rule.
---

# Encode a lint rule

The rule's failure message is the instruction the next agent reads, at the moment it makes the mistake. Write it for that reader: say what to do instead, not only what is wrong.

## 1. Start from the examples

Take the bad and good examples from `correct` or `paved-path`. If you do not have them, write them first: the smallest code that shows the mistake and its fix.

## 2. Pick the mechanism

Prefer the first option that can express the rule:

1. **A compiler or analyzer setting.** Strict modes catch whole classes of mistakes with no custom code: Swift 6 language mode, Kotlin `allWarningsAsErrors`, Dart `strict-*`, mypy `strict`, Rust `[lints]`.
2. **A built-in rule of the existing linter**, turned on or raised to error:

| Stack | Where rules live | Banning an API or import |
|---|---|---|
| Kotlin | `config/detekt/detekt.yml` | `ForbiddenImport`, `ForbiddenMethodCall` (needs `detektMain` type resolution) |
| Swift | `.swiftlint.yml` | ast-grep rule in `.agents/ast-grep/`; SwiftLint custom rules need SourceKit |
| Rust | `[lints]` in `Cargo.toml`, `clippy.toml` | `disallowed-methods`, `disallowed-types` in `clippy.toml` |
| Python | `ruff.toml`, `mypy.ini`, `.importlinter` | `[lint.flake8-tidy-imports.banned-api]` |
| Dart | `analysis_options.yaml` | `import-boundaries.mjs` for imports; `avoid_*` lints |
| Any | `.agents/import-boundaries.json`, `.agents/ratchet.json` | `import-boundaries.mjs` for layers; `ratchet.mjs` for any regex |

3. **A structural custom rule** with ast-grep (Swift, Kotlin, Rust, Python, TypeScript) when no built-in rule matches. Put it in `.agents/ast-grep/<id>.yml` next to the repository's `sgconfig.yml`. Test the pattern with `npx -y -p @ast-grep/cli ast-grep run -l <language> -p '<pattern>' <file>` before writing the rule file.
4. **A small check script** under `scripts/agent-checks/` for rules about files rather than code, such as "every locale has every key". Follow the existing scripts: `--help` with examples, exit 1 on violations, and a message that says what to do instead.

## 3. Write the message

A good message names the replacement and, if one exists, where it lives:

- Weak: "Forbidden import."
- Strong: "Use `AppDates.display(instant)` from `core/time`; `java.util.Date` loses the time zone."

## 4. Prove it

1. Put the bad example in a fixture or test file next to the rule, or in a temporary file in the repository.
2. Run the linter. It must fail and print your message.
3. Replace it with the good example. The linter must pass.
4. Run `scripts/check.sh` on the whole repository. Handle existing violations with the pack's baseline method, never by weakening the rule.

Keep the bad and good examples as committed fixtures when the linter supports them, so the rule stays proven after later edits.

## Report

The rule's file and id, its message, the failing output on the bad example, the passing output on the good one, and any baselined count.
