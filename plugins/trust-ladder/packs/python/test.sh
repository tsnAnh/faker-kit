#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

lint_imports=$(command -v lint-imports || true)
if [ -z "$lint_imports" ]; then
  python3 -m venv "$work/venv" >/dev/null
  "$work/venv/bin/pip" install --quiet import-linter
  lint_imports="$work/venv/bin/lint-imports"
fi

prepare() {
  cp -R "$pack/fixtures/$1" "$work/$1"
  cp "$pack/files/ruff.toml" "$pack/files/mypy.ini" "$pack/files/.importlinter" "$work/$1/"
}

expect_fail() {
  local what=$1 needle=$2; shift 2
  if "$@" > "$work/out.log" 2>&1; then echo "python pack: $what passed on the bad fixture" >&2; exit 1; fi
  grep -q -- "$needle" "$work/out.log" || { echo "python pack: $what did not report $needle" >&2; cat "$work/out.log" >&2; exit 1; }
}

prepare bad
cd "$work/bad"
for code in ANN201 BLE001 T201 ERA001 PGH003 TID251; do expect_fail "ruff" "$code" ruff check .; done
expect_fail "mypy" "no-untyped-def" mypy app
expect_fail "import-linter" "app.domain.order -> app.ui.format" "$lint_imports"

prepare good
cd "$work/good"
ruff check .
ruff format --check .
mypy app
"$lint_imports" > /dev/null
echo "python pack: bad fixture fails, good fixture passes"
