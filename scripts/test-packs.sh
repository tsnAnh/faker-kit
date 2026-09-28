#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/test-packs.sh [PACK...]

Prove every lint pack still works: each pack's test.sh must fail on its bad fixture
and pass on its good one. With no arguments, tests every pack.

A pack whose toolchain is missing is skipped locally and reported. In CI (CI=true)
a missing toolchain fails the run, so nothing passes by being skipped.

Examples:
  scripts/test-packs.sh
  DART=~/dart-sdk/bin/dart scripts/test-packs.sh flutter-dart
USAGE
}

[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && { usage; exit 0; }
root=$(cd "$(dirname "$0")/.." && pwd)
packs_dir="$root/plugins/trust-ladder/packs"
have() { command -v "$1" >/dev/null 2>&1; }

requirements() {
  case "$1" in
    agnostic) echo bun node git ;;
    rust) echo cargo cargo-clippy ;;
    python) echo python3 ruff mypy ;;
    kotlin-android|kmp) echo java curl ;;
    flutter-dart) echo "${DART:-dart}" ;;
    swift-ios) echo npx curl ;;
    *) echo ;;
  esac
}

if [ $# -gt 0 ]; then packs=("$@"); else mapfile -t packs < <(find "$packs_dir" -mindepth 2 -maxdepth 2 -name pack.json -printf '%h\n' | xargs -n1 basename | sort); fi

passed=() failed=() skipped=()
for pack in "${packs[@]}"; do
  missing=()
  for tool in $(requirements "$pack"); do have "$tool" || missing+=("$tool"); done
  if [ ${#missing[@]} -gt 0 ]; then
    if [ "${CI:-}" = "true" ]; then
      printf '%-15s FAIL (missing %s)\n' "$pack" "${missing[*]}"; failed+=("$pack")
    else
      printf '%-15s skip (missing %s)\n' "$pack" "${missing[*]}"; skipped+=("$pack")
    fi
    continue
  fi
  log=$(mktemp)
  if bash "$packs_dir/$pack/test.sh" > "$log" 2>&1; then
    printf '%-15s ok\n' "$pack"; passed+=("$pack")
  else
    printf '%-15s FAIL\n' "$pack"; sed 's/^/    /' "$log" | tail -40; failed+=("$pack")
  fi
  rm -f "$log"
done

printf '\ntest-packs: %d passed, %d failed, %d skipped\n' "${#passed[@]}" "${#failed[@]}" "${#skipped[@]}"
[ ${#failed[@]} -eq 0 ]
