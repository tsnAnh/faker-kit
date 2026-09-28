#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  cat <<'USAGE'
Usage: scripts/check-codex.sh [CODEX_BIN]

Add this repository as a Codex plugin marketplace in a throwaway CODEX_HOME and
install every plugin it lists. Fails if Codex rejects the marketplace or any plugin.

Example:
  npm i --prefix /tmp/codex @openai/codex && scripts/check-codex.sh /tmp/codex/node_modules/.bin/codex
USAGE
  exit 0
fi

codex=${1:-codex}
root=$(cd "$(dirname "$0")/.." && pwd)
CODEX_HOME=$(mktemp -d "${HOME}/.codex-check.XXXXXX")
export CODEX_HOME
trap 'rm -rf "$CODEX_HOME"' EXIT

"$codex" plugin marketplace add "$root" >/dev/null
marketplace=$(node -e 'console.log(require(process.argv[1]).name)' "$root/.claude-plugin/marketplace.json")
count=0
for plugin in $(node -e 'console.log(require(process.argv[1]).plugins.map((p) => p.name).join(" "))' "$root/.claude-plugin/marketplace.json"); do
  "$codex" plugin add "$plugin@$marketplace" >/dev/null
  count=$((count + 1))
done
installed=$("$codex" plugin list | grep -c "installed, enabled" || true)
if [ "$installed" -ne "$count" ]; then
  printf 'check-codex: installed %s of %s plugins\n' "$installed" "$count" >&2
  "$codex" plugin list >&2
  exit 1
fi
printf 'check-codex: %s plugins install in Codex\n' "$count"
