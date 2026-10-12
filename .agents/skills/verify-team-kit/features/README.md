# Team kit verification map

The maintained source for verifying what the team kit's users meet. Read this index, then use the matching feature file as the recipe.

| Feature | File | Where it runs |
|---|---|---|
| Install the Hermes module | `hermes-install.md` | sandbox |
| Set up a project and run its bot's commands | `project-bot.md` | sandbox |
| The plugins load in Claude Code, Codex, and Pi | `agents-load-the-kit.md` | this checkout |
| Sync the vendored plugins from upstream | `upstream-sync.md` | this checkout |
| Build the docs site | `docs-site.md` | this checkout |

## Baseline preconditions

- The change is committed: the sandbox installs `HEAD`.
- For sandbox features: `sandbox.sh up` printed a directory and `sandbox.sh doctor` passed.
- `python3` (3.10 or newer), `node` (22), `git`, and `bash` are on `PATH`; `bun` for the plugin tests.
- Never drive your own `~/.hermes`: every Hermes command runs through `sandbox.sh run` or `sandbox.sh evidence`.

## Proof and skip reporting

- Each claim cites a transcript in `.verify-evidence/<run>/` and the line in it that proves the claim.
- A step that needs a tool the session lacks (Codex, Pi, network) is reported as skipped with the command tried and what was missing; never as passed.
