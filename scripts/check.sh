#!/usr/bin/env bash
# The single check command: the fast checks from AGENTS.md. CI runs the same script.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 .agents/team-kit/vendor_kit.py --check .
node scripts/sync-upstream.mjs --check
node scripts/validate.mjs
node scripts/gen-catalog.mjs --check
node scripts/gen-site.mjs
bun test plugins/trust-ladder
bun test upstream/agent-hooks
