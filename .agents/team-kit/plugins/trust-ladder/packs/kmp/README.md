# Kotlin Multiplatform pack

Install the kotlin-android pack first; this pack layers on top of it.

| Rule | Stops agents from |
|---|---|
| detekt `ForbiddenImport` scoped to `commonMain` and `commonTest`: `java.*`, `javax.*`, `android.*`, `androidx.*`, `platform.*` | Reaching for a platform API in shared code. When every target runs on the JVM, `commonMain` can compile against JDK types, so the mistake only surfaces the day someone adds iOS. |
| detekt `ForbiddenImport`: `kotlinx.coroutines.runBlocking` in shared code | Blocking in code that must also run on targets without `runBlocking`. |
| `explicitApi()` | Leaking internals as public API of the shared module. |
| `allWarningsAsErrors` on every target | Warnings on one target going unnoticed because nobody builds it locally. |

The `imports` list in `detekt-kmp.yml` replaces the kotlin-android list for the source sets it includes, so add any of your paved-path bans to both files.

Duplicated logic in `androidMain` and `iosMain` that belongs in `commonMain` is a gardener finding: the gardener scan lists helpers defined in several files.

## Proof

`test.sh` runs detekt with both configs on `fixtures/bad`, which must flag the platform imports in `commonMain` and nothing in `androidMain`, and on `fixtures/good`, an expect/actual version that must pass.
