# Python pack

| Rule | Stops agents from |
|---|---|
| `ANN` (annotations) and mypy `strict` | Writing untyped functions that hide bugs until runtime. |
| `TID251` banned `typing.Any` | Escaping the type system. Parse untyped input into a typed object at the boundary. |
| `PGH003`, mypy `ignore-without-code`, `warn_unused_ignores` | Blanket `# type: ignore` that silences everything, or ignores left after the bug is fixed. |
| `BLE001`, `TRY`, `S110` | `except Exception: pass` and other swallowed errors. |
| `T20` | `print` debugging left in shipped code. |
| `ERA001` | Commented-out code, which agents read as a hint to restore it. |
| `TID252` (parents) | `from ..x import y` chains that hide which package owns a module. |
| import-linter `layers` | Lower layers importing higher ones, such as domain code importing UI. |
| `ruff format` | Style churn in diffs. |
| mypy `exhaustive-match` | A `match` over an enum that silently ignores a new member. |
| `PLC0415` | Imports hidden inside functions, which hide a module's real dependencies. |

## Install

Run `node skills/setup-faker-kit/scripts/install-pack.mjs python` from the trust-ladder plugin. It copies `ruff.toml`, `mypy.ini`, and `.importlinter`. Edit `.importlinter` so `root_package` and `layers` match the repository.

## Existing violations

`ruff check --add-noqa .` marks each existing violation with a targeted `# noqa: CODE`. Ratchet the markers with the agnostic pack so their count only goes down.

## Proof

`test.sh` runs ruff, mypy, and import-linter with this configuration on `fixtures/bad`, which must fail on each rule above, and on `fixtures/good`, which must pass.
