# Agnostic pack

Checks every repository gets, whatever its stack. They need only Node 18 or newer and git.

| Check | Stops agents from | Config |
|---|---|---|
| `scripts/check.sh` | Guessing which commands prove a change. One entry point runs everything CI runs. | `.agents/check-commands`, one command per line |
| `ratchet.mjs` | Spreading a pattern you are cleaning up. Existing instances are allowed; new ones fail. | `.agents/ratchet.json` rules; `.agents/ratchet-baseline.json` counts |
| `import-boundaries.mjs` | Reaching across layers, such as UI code calling the network or `commonMain` importing Android. Covers Dart, TypeScript, Python, Kotlin, Java, Swift, and Rust imports. | `.agents/import-boundaries.json` |
| `rule-fixtures.mjs` | Weakening or deleting a rule someone encoded. Each rule's bad fixture must still fail with the expected message, and its good fixture must pass. pstack's `/correct` registers each new rule here. | `.agents/rule-fixtures.json`; fixtures in `.agents/rule-fixtures/<id>/` |
| `no-new-comments.mjs` (optional) | Explaining workarounds in comments, which the next agent copies along with the workaround. Checks only lines added since the base branch, and only source code: YAML, TOML, and Markdown are skipped. Each changed file is read whole, so `//` inside a string, template literal, regex, or heredoc does not count. | `.agents/no-new-comments.json`: `mode`, `allow`, `exclude` |
| `locale-keys.mjs` (optional) | Shipping a string in one language only, copying the source text into other locales, or dropping a placeholder. Reads ARB, JSON, Android `strings.xml`, and Apple `.strings`. | `.agents/locales.json` |
| `lefthook.yml` | Pushing without running the checks. | `pre-push` runs `scripts/check.sh` |

Every script prints `--help` with examples, and every failure message says what to do instead.

## Stop the bleeding with the ratchet

When a pattern is everywhere and cannot be removed today, ratchet it instead of ignoring it:

```json
{ "rules": [ { "id": "kotlin-not-null-assertion", "pattern": "[A-Za-z0-9_)\\]]!!", "files": ["*.kt"], "message": "Handle null explicitly; see AGENTS.md > Paved paths." } ] }
```

Run `node scripts/agent-checks/ratchet.mjs update` to record today's counts. CI then fails when any count rises. Run `update` again after a cleanup to lock in the lower number; it never raises a baseline.

Stack packs use the same idea for linters without a native baseline: existing violations get a suppression carrying the reason `faker-kit baseline`, and a ratchet rule counts those suppressions so they can only disappear.
