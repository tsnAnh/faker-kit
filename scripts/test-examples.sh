#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/test-examples.sh

Compile and lint the code examples in the rust, python, and dart best-practices
skills with the matching lint pack's configuration, so the skills never teach code
the packs would reject. Skips a language whose toolchain is missing, except in CI.
USAGE
}
[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && { usage; exit 0; }

root=$(cd "$(dirname "$0")/.." && pwd)
plugin="$root/plugins/trust-ladder"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failed=0

extract() {
  python3 - "$1" "$2" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
match = re.search(r"```" + sys.argv[2] + r"\n(.*?)```", text, re.S)
print(match.group(1), end="")
PY
}

run() {
  local name=$1; shift
  local missing=()
  for tool in $1; do command -v "$tool" >/dev/null 2>&1 || missing+=("$tool"); done
  shift
  if [ ${#missing[@]} -gt 0 ]; then
    if [ "${CI:-}" = "true" ]; then printf '%-7s FAIL (missing %s)\n' "$name" "${missing[*]}"; failed=1; else printf '%-7s skip (missing %s)\n' "$name" "${missing[*]}"; fi
    return
  fi
  if "$@" > "$work/$name.log" 2>&1; then printf '%-7s ok\n' "$name"; else printf '%-7s FAIL\n' "$name"; sed 's/^/    /' "$work/$name.log"; failed=1; fi
}

check_rust() {
  mkdir -p "$work/rust/src"
  printf '[package]\nname = "patterns"\nversion = "0.1.0"\nedition = "2021"\npublish = false\n\n[dependencies]\nthiserror = "2"\n\n' > "$work/rust/Cargo.toml"
  cat "$plugin/packs/rust/merge/cargo-lints.toml" >> "$work/rust/Cargo.toml"
  cp "$plugin/packs/rust/files/clippy.toml" "$work/rust/"
  extract "$plugin/skills/rust-best-practices/references/patterns.md" rust > "$work/rust/src/lib.rs"
  cd "$work/rust" && cargo clippy --quiet --all-targets -- -D warnings && cargo test --quiet
}

check_python() {
  mkdir -p "$work/python"
  cp "$plugin/packs/python/files/ruff.toml" "$plugin/packs/python/files/mypy.ini" "$work/python/"
  extract "$plugin/skills/python-best-practices/references/patterns.md" python > "$work/python/cart.py"
  cd "$work/python" && ruff check . && ruff format --check . && mypy cart.py
}

check_dart() {
  local dart=${DART:-dart}
  mkdir -p "$work/dart/lib"
  printf 'name: app\npublish_to: none\nenvironment:\n  sdk: ^3.5.0\ndependencies:\n  logging: ^1.2.0\ndev_dependencies:\n  lints: ">=5.0.0 <7.0.0"\n' > "$work/dart/pubspec.yaml"
  sed 's|^include: package:flutter_lints/flutter.yaml$|include: package:lints/recommended.yaml|' "$plugin/packs/flutter-dart/files/analysis_options.yaml" > "$work/dart/analysis_options.yaml"
  extract "$plugin/skills/dart-best-practices/references/patterns.md" dart > "$work/dart/lib/cart.dart"
  cd "$work/dart" && "$dart" pub get > /dev/null && "$dart" analyze --fatal-infos && "$dart" format --output=none --set-exit-if-changed lib
}

run rust "cargo" check_rust
run python "python3 ruff mypy" check_python
run dart "${DART:-dart}" check_dart
exit $failed
