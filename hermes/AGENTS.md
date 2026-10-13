# AGENTS.md for hermes/

Rules here apply only to files under `hermes/`. The root AGENTS.md, including its Hermes invariants, still applies.

- `bin/` holds the commands a bot or the owner runs; `lib/` holds what they share; `plugin/team_kit_guard/` is the guard and depends on neither. `.agents/import-boundaries.json` enforces it.
- Every decision a command makes reads the clock and GitHub through injected parameters (`now`, `run`, `cloud`, `fetch`), so `tests/` drives the real code with doubles. Keep new code testable the same way; never call `time.time()` or `subprocess` directly inside a decision.
- `tests/fake_gh.py` and `tests/fake_hermes.py` model the real `gh` and `hermes` CLIs. When code needs a call they do not answer, extend the double the way the real CLI behaves, never to make a test pass.
- A change a project bot or the owner can observe needs the `verify-team-kit` skill's evidence (`.agents/skills/verify-team-kit/features/`), not only `pytest`.
- A Claude Code session's task is its first message; signing keys go to cloud agents on stdin or as a follow-up message (`lib/team_kit_cloud.py`), never in argv.
