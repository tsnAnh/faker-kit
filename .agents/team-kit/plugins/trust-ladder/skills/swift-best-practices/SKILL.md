---
name: swift-best-practices
description: Swift best practices for iOS, macOS, and Swift packages. Use when reading or editing any .swift file.
paths: ["**/*.swift"]
---

# Swift best practices

Apply the **principle-type-system-discipline** principle skill from pstack first. This table grounds it in Swift. Examples: `references/patterns.md`.

| Rule | Summary |
|------|---------|
| Enums for variants | Model states as an `enum` with associated values. No `isLoading` flag next to optional data and error. |
| Exhaustive `switch` | Switch over your own enums with no `default`, so a new case fails to compile. Use `@unknown default` only for enums from other modules. |
| Tagged ids | Wrap ids in a small `struct` (`struct OrderID: Hashable, Sendable { let rawValue: String }`) so they cannot be swapped. Validate in one failable initializer. |
| No force operations | No `!`, `try!`, or `as!`. Unwrap with `guard let` where the value enters; after that, types carry the guarantee. |
| Parse at the boundary | Decode with `Codable` into DTOs in the data layer, then map to domain types. Views and view models never see raw dictionaries. |
| Typed errors | Use `throws(CheckoutError)` for failures callers must handle, so the compiler lists the cases. |
| Value types by default | `struct` and `enum` by default; `final class` or `actor` only when identity or shared mutable state is the point. |
| Swift concurrency | `@MainActor` on UI types, `actor` for shared mutable state, `Sendable` across tasks, `async let` or task groups for parallel work. No `DispatchQueue.main.async`. |
| Access control | `private` or `internal` by default; `public` only at a package's edge. |
| Real tests | Swift Testing (`@Test`, `#expect`) with in-memory implementations over mocks. Verify UI in the simulator with the verification skill. |
| Logging | `os.Logger` with a subsystem and category. No `print` or `NSLog`. |
