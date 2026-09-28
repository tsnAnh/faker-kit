#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
dart=${DART:-dart}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

prepare() {
  cp -R "$pack/fixtures/$1" "$work/$1"
  sed 's|^include: package:flutter_lints/flutter.yaml$|include: package:lints/recommended.yaml|' "$pack/files/analysis_options.yaml" > "$work/$1/analysis_options.yaml"
  (cd "$work/$1" && "$dart" pub get > /dev/null)
}

prepare bad
if (cd "$work/bad" && "$dart" analyze --fatal-infos > "$work/bad.log" 2>&1); then
  echo "flutter-dart pack: the analyzer passed the bad fixture" >&2; exit 1
fi
for code in todo always_use_package_imports avoid_dynamic_calls avoid_print avoid_catches_without_on_clauses always_declare_return_types discarded_futures no_default_cases; do
  grep -q " - $code$" "$work/bad.log" || { echo "flutter-dart pack: expected $code on the bad fixture" >&2; cat "$work/bad.log" >&2; exit 1; }
done
if grep -q "undefined_lint\|isn't a recognized lint" "$work/bad.log"; then
  echo "flutter-dart pack: analysis_options.yaml names a lint this Dart SDK does not know" >&2; cat "$work/bad.log" >&2; exit 1
fi

prepare good
(cd "$work/good" && "$dart" analyze --fatal-infos)
echo "flutter-dart pack: bad fixture fails, good fixture passes"
