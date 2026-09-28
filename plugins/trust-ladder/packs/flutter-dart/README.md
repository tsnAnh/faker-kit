# Flutter and Dart pack

| Rule | Stops agents from |
|---|---|
| `strict-casts`, `strict-inference`, `strict-raw-types`, `avoid_dynamic_calls` | Letting `dynamic` spread through the app: unchecked casts, untyped collections, calls that fail at runtime. |
| `todo`, `fixme`, `hack` as errors | Leaving a note that tells the next agent the shortcut is fine. |
| `always_use_package_imports`, `avoid_relative_lib_imports` | Relative imports that hide which feature a file depends on. It also makes the import-boundary rules reliable. |
| `unawaited_futures`, `discarded_futures`, `use_build_context_synchronously` | Fire-and-forget futures and using a `BuildContext` after an `await`, the classic async bugs in Flutter UI. |
| `avoid_catches_without_on_clauses`, `only_throw_errors` | `catch (e)` that swallows every error. |
| `avoid_print` | Debug output in shipped builds. |
| `always_declare_return_types`, `prefer_final_locals`, `require_trailing_commas` | Code whose types and diffs are harder to read. |
| `import-boundaries.mjs` (agnostic pack) with `examples/import-boundaries.json` | UI importing a feature's data layer, or features importing each other. |
| `locale-keys.mjs` (agnostic pack) with `examples/locales.json` | Adding a string to one ARB file only, or pasting English into the other locales. |

## Install

Run `install-pack.mjs agnostic` and then `install-pack.mjs flutter-dart` from the trust-ladder plugin. Add `flutter_lints` as a dev dependency if it is missing. Copy the example configs into `.agents/` and adjust their globs.

## Proof

`test.sh` runs `dart analyze --fatal-infos` with this configuration on `fixtures/bad`, which must fail on each rule above, and on `fixtures/good`, which must pass. The fixtures are plain Dart packages, so the test swaps the `flutter_lints` include for `package:lints`. It also fails if the SDK does not recognize a lint the config names.
