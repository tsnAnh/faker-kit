# The plugins load in Claude Code, Codex, and Pi

Teams install the kit as a plugin marketplace. Each agent must accept every plugin and discover every skill.

## Sub-features

- `claude-validate`: Claude Code validates the marketplace and each plugin.
- `codex-install`: Codex adds the marketplace and installs every plugin, in a throwaway `CODEX_HOME`.
- `pi-discover`: Pi discovers every skill and loads the trust-ladder extension.

## How to get to it (user POV)

- `/plugin marketplace add tsnAnh/team-kit` in Claude Code; the same repository as a Codex marketplace; the Pi package.

## Driving it in this checkout

- **Claude Code.** `claude plugin validate .` and `for p in plugins/*/; do claude plugin validate "$p"; done`. Proof: `Validation passed` for each.
- **Codex.** `scripts/check-codex.sh codex` (install Codex first with `npm i --prefix /tmp/agents @openai/codex` if absent). Proof: exit 0.
- **Pi.** `node scripts/check-pi-package.mjs --sdk <dir with @earendil-works/pi-coding-agent installed>`. Proof: exit 0 and the skill count it prints.

## Gotchas

- These need the agents' CLIs and network access. Without them, report the step as skipped with the missing tool; CI's `agents-load-the-kit` job runs all three.
