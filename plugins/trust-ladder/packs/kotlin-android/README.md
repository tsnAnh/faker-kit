# Kotlin and Android pack

| Rule | Stops agents from |
|---|---|
| detekt `UnsafeCallOnNullableType`, `UnsafeCast` | `!!` and unchecked `as` casts that crash on the first unexpected null. These need type resolution, which the Gradle `detektMain` task provides. |
| detekt `ForbiddenComment` (TODO, FIXME, HACK, XXX, "for now", "workaround") | Leaving temporary code with a note that tells the next agent it is fine. |
| detekt `TooGenericExceptionCaught`, `SwallowedException` | `catch (e: Exception) {}` that hides failures. |
| detekt `GlobalCoroutineUsage`, `InjectDispatcher` | `GlobalScope` and hard-coded dispatchers that make code untestable. |
| detekt `ForbiddenImport` (`java.util.Date`, `android.util.Log`) | Using APIs the team replaced. Add your own paved paths here with a `reason` that names the replacement. |
| ktlint, with every wildcard import banned | Formatting churn and `import x.*` that hides dependencies. |
| Android Lint as errors (`HardcodedText`, `MissingTranslation`, ...) | Hard-coded UI strings and untranslated locales. |
| Kotlin `allWarningsAsErrors` | Deprecations and unchecked casts piling up as warnings nobody reads. |
| detekt `ElseCaseInsteadOfExhaustiveWhen` | An `else` branch over a sealed type, which hides every variant added later. |

Layer boundaries, such as UI not importing `com.example.data`, belong in the agnostic pack's `.agents/import-boundaries.json`, which reads Kotlin imports. Konsist architecture tests are an alternative when the team prefers tests in Kotlin.

## Install

Run `node skills/setup-faker-kit/scripts/install-pack.mjs kotlin-android` from the trust-ladder plugin. It copies `config/detekt/detekt.yml`, `.editorconfig`, and `lint.xml`. Merge `merge/build.gradle.kts` into each module, or better into a convention plugin every module applies.

## Existing violations

detekt, Android Lint, and ktlint each have a native baseline. Generate them once with the commands in `pack.json` and commit them. New code must pass without adding to them.

## Proof

`test.sh` downloads the pinned detekt and ktlint (checksums in `../tools.sh`), runs them on `fixtures/bad`, which must fail on the rules above, and on `fixtures/good`, which must pass.
