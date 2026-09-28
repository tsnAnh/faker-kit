---
name: dart-best-practices
description: Dart and Flutter best practices. Use when reading or editing any .dart file, pubspec.yaml, or analysis_options.yaml.
paths: ["**/*.dart", "**/pubspec.yaml", "**/analysis_options.yaml"]
---

# Dart and Flutter best practices

Apply the **principle-type-system-discipline** principle skill from pstack first. This table grounds it in Dart 3. Examples: `references/patterns.md`, checked with `dart analyze --fatal-infos` in the flutter-dart lint pack's configuration.

| Rule | Summary |
|------|---------|
| Sealed variants | Model states as a `sealed class` with one subclass per state. No `isLoading` flag next to nullable data and error. |
| Exhaustive `switch` | Use switch expressions over sealed classes and enums with no `default` (`no_default_cases`), so a new state fails to compile. |
| Extension types for ids | `extension type const OrderId._(String value)` with a parsing factory, so ids cannot be swapped with plain strings. |
| No `dynamic` | Strict analyzer modes are on. Decode JSON as `Object?` and destructure it with patterns; `as` only after an `is` check. |
| Parse at the boundary | Turn JSON into immutable models in the data layer (a `fromJson` with pattern matching, or `json_serializable`). Widgets never see maps. |
| Immutable by default | `final` fields, `const` constructors, and unmodifiable collections. |
| Async discipline | Await futures or mark them with `unawaited(...)`. After an `await` in a widget, check `context.mounted` before using the context. |
| One state approach | The repository's paved path for state management (listed in AGENTS.md). Do not add a second library. |
| Feature folders and package imports | `lib/features/<feature>/{ui,data,...}` with `package:` imports only; import boundaries are checked. |
| Every locale | A new user-facing string goes into every ARB file with a real translation (`locale-keys` checks it). |
| Real tests | Widget tests with real widgets and in-memory repositories. Verify on a device or emulator with the verification skill. |
| Logging | `package:logging` or `dart:developer` `log`. No `print`. |
