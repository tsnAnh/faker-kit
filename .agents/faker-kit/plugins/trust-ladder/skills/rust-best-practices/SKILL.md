---
name: rust-best-practices
description: Rust best practices for libraries, services, and CLIs. Use when reading or editing any .rs file or Cargo.toml.
paths: ["**/*.rs", "**/Cargo.toml"]
---

# Rust best practices

Apply the **principle-type-system-discipline** principle skill from pstack first. This table grounds it in Rust. Examples: `references/patterns.md`, each compiled with clippy in the rust lint pack's configuration.

| Rule | Summary |
|------|---------|
| Enums for variants | Model states as an `enum` with data in each variant. No `bool` flag next to `Option` fields. |
| Exhaustive `match` | Match your own enums without `_ =>`, so a new variant fails to compile. Mark public enums `#[non_exhaustive]` only when downstream crates must tolerate growth. |
| Newtypes for ids | `pub struct OrderId(String)` with a private field and a `TryFrom<&str>` that validates once. |
| Constructive types | Make illegal values unconstructible: a non-empty list as `(T, Vec<T>)`, a typestate type per lifecycle stage. |
| No panics in library code | No `unwrap`, `expect`, `panic!`, `todo!`, or indexing that can panic outside tests. Return `Result` and propagate with `?`. |
| Error enums | Use a `thiserror` enum per module for errors callers handle; `anyhow` only in binaries. |
| Parse at the boundary | Deserialize with `serde` into DTOs, then convert with `TryFrom` into domain types. Code past the boundary never sees raw strings. |
| Borrow before cloning | Take `&str`, `&[T]`, and `impl AsRef` in parameters. A `.clone()` to satisfy the borrow checker is a design smell to fix. |
| Concurrency | Prefer ownership and channels over `Arc<Mutex<_>>`. Never block inside `async`; use `spawn_blocking`. |
| Suppressions | `#[expect(lint, reason = "...")]`, never `#[allow]`: an expectation fails once the code is fixed. |
| Real tests | Test through the public API. Use `proptest` for parsers. Verify binaries end to end with the verification skill. |
| Logging | `tracing` with structured fields. No `println!` or `dbg!`. |
