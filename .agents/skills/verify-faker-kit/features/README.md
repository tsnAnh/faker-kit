# faker-kit verification map

The maintained source for verifying what faker-kit's users meet. Read this index, then use the matching feature file as the recipe.

| Feature | File | Where it runs |
|---|---|---|
| Install the Hermes module | `hermes-install.md` | e2e run |
| Set up a project and run its bot's commands | `project-bot.md` | e2e run |
| The plugins load in Claude Code, and their hooks and scripts run | `agents-load-the-kit.md` | this checkout |
| Sync the vendored plugins from upstream | `upstream-sync.md` | this checkout |

## Baseline preconditions

- For the e2e features: `hermes/e2e/run_dispatch_e2e.py` is the entry point and needs only `python3` with PyYAML and `git`.
- `python3` (3.10 or newer), `node` (22), `git`, and `bash` are on `PATH`; `bun` and `jq` for `scripts/e2e/kit-e2e.mjs` (the continual-learning and advisor hooks).
- Never drive your own `~/.hermes`: the e2e runs in a throwaway home.

## Proof and skip reporting

- Each claim cites a transcript in the saved e2e artifact and the line in it that proves the claim.
- A step that needs a tool the session lacks (Claude Code, network) is reported as skipped with the command tried and what was missing; never as passed.
