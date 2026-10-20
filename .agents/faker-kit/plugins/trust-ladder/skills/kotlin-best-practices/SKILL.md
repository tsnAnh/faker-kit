---
name: kotlin-best-practices
description: Kotlin best practices for Android, KMP, and JVM code. Use when reading or editing any .kt or .kts file.
paths: ["**/*.kt", "**/*.kts"]
---

# Kotlin best practices

Apply the **principle-type-system-discipline** principle skill from pstack first. This table grounds it in Kotlin. Examples: `references/patterns.md`.

| Rule | Summary |
|------|---------|
| Sealed variants | Model states as a `sealed interface` with one type per state. No `isLoading` boolean next to nullable `data` and `error`. |
| Exhaustive `when` | Use `when` as an expression over sealed types and enums with no `else`, so a new variant fails to compile. detekt `ElseCaseInsteadOfExhaustiveWhen` enforces it. |
| Value classes for ids | `@JvmInline value class UserId(val value: String)` so ids cannot be swapped. Validate in one factory. |
| No `!!` | Handle null where it enters: return a sealed failure, or use `requireNotNull(x) { "why" }` only at a boundary. |
| Parse at the boundary | Decode JSON and platform types into domain types in the data layer. Nothing past that point sees raw maps or nullable DTO fields. |
| Expected failures are values | Return a sealed result for failures callers must handle. Throw only for bugs. |
| Immutable by default | `val`, read-only `List`/`Map`, `data class` with `copy`. `var` needs a reason the reader can see. |
| Structured concurrency | Launch in a scope someone owns (`viewModelScope`, an injected `CoroutineScope`). No `GlobalScope`. Inject dispatchers. |
| Streams | Expose `StateFlow` for UI state and `Flow` for events from repositories. Collect with lifecycle awareness in UI. |
| Visibility | `internal` or `private` by default; `public` only at a module's edge (`explicitApi()` in libraries). |
| Real tests | Use fakes that implement the interface over mocks. `runTest` with a test dispatcher for coroutines. Verify UI in the running app with the verification skill. |
| Logging | The app's logger with structured fields. No `println` or `android.util.Log` in shipped code. |
