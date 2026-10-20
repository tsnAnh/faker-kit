# Swift and iOS pack

| Rule | Stops agents from |
|---|---|
| SwiftLint `force_unwrapping`, `force_cast`, `force_try`, `implicitly_unwrapped_optional` (errors) | Crashing on `!`, `as!`, and `try!` instead of handling the failure. |
| SwiftLint `todo` (error) | Leaving TODO and FIXME notes that tell the next agent the shortcut is fine. |
| SwiftLint `weak_delegate`, `unowned_variable_capture`, `private_swiftui_state`, and other opt-in rules | Retain cycles, unsafe captures, and leaky SwiftUI state. |
| ast-grep `swift-no-print` | `print`, `debugPrint`, and `NSLog` instead of `os.Logger`. |
| ast-grep `swift-no-workaround-comments` | Comments that excuse a hack ("for now", "temporary", "workaround"). |
| ast-grep `swift-no-dispatch-main-async` | `DispatchQueue.main.async` instead of `@MainActor`, which Swift 6 can check. |
| Swift 6 language mode, `SWIFT_STRICT_CONCURRENCY = complete`, warnings as errors | Data races and concurrency warnings that never get fixed. |

The ast-grep rules live in `.agents/ast-grep/` with `sgconfig.yml` at the repository root. They cover what SwiftLint custom rules would, but they run on Linux too: SwiftLint's custom rules need SourceKit, which only a Swift toolchain provides.

## Install

Run `node skills/setup-faker-kit/scripts/install-pack.mjs swift-ios` from the trust-ladder plugin. Then set `Config/Strict.xcconfig` as each target's base configuration, or add the settings from `merge/Package.swift` to each Swift package.

## Proof

`test.sh` runs SwiftLint (the pinned static build on Linux) and the ast-grep rules on `fixtures/bad`, which must fail on every rule above, and on `fixtures/good`, which must pass.
