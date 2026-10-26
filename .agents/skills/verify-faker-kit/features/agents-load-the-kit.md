# The plugins load in Claude Code

Teams install the kit as a plugin marketplace. Claude Code must accept every plugin and discover every skill.

## Sub-features

- `claude-validate`: Claude Code validates the marketplace and each plugin.
- `plugin-scripts`: the SessionStart and UserPromptSubmit hooks, the hook adapter, `link-skills.sh`, and `install-kit-skills.sh` run for real, and every plugin installs.

## How to get to it (user POV)

- `/plugin marketplace add tsnAnh/faker-kit` in Claude Code.

## Driving it in this checkout

`claude plugin validate .` and `for p in plugins/*/; do claude plugin validate "$p"; done`. Proof: `Validation passed` for each.

`node scripts/e2e/kit-e2e.mjs --artifact <path>`. Proof: every step prints `ok`, and two runs print the same digest. The step starting "with the real claude CLI" prints `skip` when the CLI is missing, and fails when it is missing and `CI` is set.

## Gotchas

- This needs the Claude Code CLI. Without it, report the step as skipped with the missing tool; CI's `agents-load-the-kit` job runs it.
