#!/usr/bin/env bash
# Exports the Mintlify site in site/ to static files in site/dist/, ready for deploy/docs/Dockerfile.
# Needs internet: mint downloads its renderer from releases.mintlify.com.
#
#   bash scripts/export-site.sh
#   docker build -f deploy/docs/Dockerfile -t team-kit-docs .
set -euo pipefail

MINT_VERSION="${MINT_VERSION:-4.2.942}"
root="$(cd "$(dirname "$0")/.." && pwd)"
zip="$(mktemp -d)/site.zip"

node "$root/scripts/gen-site.mjs"
(cd "$root/site" && npx --yes "mint@${MINT_VERSION}" export --output "$zip")
rm -rf "$root/site/dist"
unzip -q "$zip" -d "$root/site/dist"
rm -f "$root/site/dist/serve.js" "$root/site/dist/Start Docs.command" "$root/site/dist/Start Docs.bat"
test -f "$root/site/dist/index.html"
echo "export-site: $(find "$root/site/dist" -name index.html | wc -l | tr -d ' ') pages in site/dist"
