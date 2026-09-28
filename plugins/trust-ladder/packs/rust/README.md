# Rust pack

| Rule | Stops agents from |
|---|---|
| `clippy::unwrap_used`, `expect_used`, `panic` (deny) | Crashing on a `None` or `Err` in library code instead of returning a `Result`. Tests may still unwrap (`clippy.toml`). |
| `clippy::todo`, `unimplemented` (deny) | Leaving placeholders that compile and then panic in production. |
| `clippy::dbg_macro`, `print_stdout`, `print_stderr` (deny) | Shipping debug output instead of using the logger. |
| `clippy::allow_attributes`, `allow_attributes_without_reason` (deny) | Silencing a lint without saying why. Suppressions must be `#[expect(lint, reason = "...")]`, which fails once the code is fixed. |
| `clippy::pedantic` (warn, fails with `-D warnings`) | Sloppy public APIs: missing `# Errors` docs, missing `#[must_use]`, needless allocations. |
| `unsafe_code` (forbid) | Reaching for `unsafe` to get around the borrow checker. |
| `rustfmt` | Style churn in diffs. |
| `cargo deny` | Pulling in yanked, vulnerable, git-sourced, or unlicensed crates. |

## Install

Run `node skills/setup-lint-pack/scripts/install-pack.mjs rust` from the trust-ladder plugin, then merge `merge/cargo-lints.toml` into `Cargo.toml` as the script prints. `clippy.toml` and `deny.toml` are copied as they are.

## Existing violations

Clippy has no baseline file. Mark each file that already violates a rule with an inner attribute that names the reason:

```rust
#![expect(clippy::unwrap_used, reason = "team-kit baseline")]
```

Then ratchet the marker so it can only disappear. Add `{"id": "rust-baseline", "pattern": "team-kit baseline", "files": ["*.rs"]}` to `.agents/ratchet.json` and run `node scripts/agent-checks/ratchet.mjs update`.

## Proof

`test.sh` runs clippy with this configuration on `fixtures/bad`, which must fail on every rule above, and on `fixtures/good`, which must pass.
