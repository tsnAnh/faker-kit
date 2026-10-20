#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
swiftlint=${SWIFTLINT:-$("$pack/../tools.sh" swiftlint)}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

prepare() {
  cp -R "$pack/fixtures/$1" "$work/$1"
  cp -R "$pack/files/." "$work/$1/"
}

prepare bad
if (cd "$work/bad" && "$swiftlint" lint --strict --quiet > "$work/bad.log" 2>&1); then
  echo "swift-ios pack: swiftlint passed the bad fixture" >&2; exit 1
fi
for rule in force_cast force_try force_unwrapping implicitly_unwrapped_optional todo sorted_imports; do
  grep -q "($rule)" "$work/bad.log" || { echo "swift-ios pack: swiftlint did not report $rule" >&2; cat "$work/bad.log" >&2; exit 1; }
done
if (cd "$work/bad" && npx -y -p @ast-grep/cli ast-grep scan > "$work/sg.log" 2>&1); then
  echo "swift-ios pack: ast-grep passed the bad fixture" >&2; exit 1
fi
for rule in swift-no-dispatch-main-async swift-no-print swift-no-workaround-comments; do
  grep -q "$rule" "$work/sg.log" || { echo "swift-ios pack: ast-grep did not report $rule" >&2; cat "$work/sg.log" >&2; exit 1; }
done

prepare good
(cd "$work/good" && "$swiftlint" lint --strict --quiet)
(cd "$work/good" && npx -y -p @ast-grep/cli ast-grep scan)
echo "swift-ios pack: bad fixture fails, good fixture passes"
