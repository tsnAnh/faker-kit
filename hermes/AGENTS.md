# AGENTS.md for hermes/

Rules here apply only to files under `hermes/`. The root AGENTS.md, including its Hermes invariants, still applies.

- `bin/` holds the commands a bot or the owner runs; `lib/` holds what they share; `plugin/faker_kit_guard/` is the guard and depends on neither. `.agents/import-boundaries.json` enforces it.
- Every decision a command makes reads the clock and GitHub through injected parameters (`now`), so `e2e/` drives the real code with stubs. Keep new code drivable the same way; never call `time.time()` or `subprocess` directly inside a decision. There are no unit tests here: `e2e/run_dispatch_e2e.py` is the only test, and each section drives a real entry point (the `faker-kit` command, `install.sh`, `environment-setup.sh`, `ratchet.mjs`, the runner slot process, and the guard plugin loaded and called the way Hermes does).
- `e2e/run_dispatch_e2e.py` stubs `gh`, `claude`, and `hermes`. When code needs a call a stub does not answer, extend the stub the way the real CLI behaves, never to make a check pass.
- A change a project bot or the owner can observe needs the `verify-faker-kit` skill's evidence (`.agents/skills/verify-faker-kit/features/`) and a step in `e2e/run_dispatch_e2e.py` that fails without the change. A new guard rule adds its commands to `e2e/guard-calls.json` (a block or allow verdict per tool call); a new stateful guard behavior adds a step to `e2e/guard_scenario.py`.
- A Claude Code session's task is its first message and never holds a secret (`lib/faker_kit_cloud.py`).
