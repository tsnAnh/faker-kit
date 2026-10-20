#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
tools="$pack/../tools.sh"
detekt=$("$tools" detekt)
ktlint=$("$tools" ktlint)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

run_detekt() {
  java -jar "$detekt" --build-upon-default-config --config "$pack/files/config/detekt/detekt.yml" --classpath "$detekt" --jvm-target 17 --input "$1" "${@:2}"
}

if run_detekt "$pack/fixtures/bad" > "$work/detekt.log" 2>&1; then
  echo "kotlin-android pack: detekt passed the bad fixture" >&2; exit 1
fi
for rule in UnsafeCallOnNullableType ElseCaseInsteadOfExhaustiveWhen ForbiddenComment ForbiddenImport WildcardImport TooGenericExceptionCaught; do
  grep -q "$rule" "$work/detekt.log" || { echo "kotlin-android pack: detekt did not report $rule" >&2; cat "$work/detekt.log" >&2; exit 1; }
done
cp "$pack/files/.editorconfig" "$work/"
cp -R "$pack/fixtures/bad" "$work/bad"
if (cd "$work" && "$ktlint" "bad/**/*.kt" > "$work/ktlint.log" 2>&1); then
  echo "kotlin-android pack: ktlint passed the bad fixture" >&2; exit 1
fi
grep -q "no-wildcard-imports" "$work/ktlint.log" || { echo "kotlin-android pack: ktlint did not report no-wildcard-imports" >&2; cat "$work/ktlint.log" >&2; exit 1; }

run_detekt "$pack/fixtures/good" > "$work/good.log" 2>&1 || { echo "kotlin-android pack: detekt failed the good fixture" >&2; cat "$work/good.log" >&2; exit 1; }
cp -R "$pack/fixtures/good" "$work/good"
(cd "$work" && "$ktlint" "good/**/*.kt")
echo "kotlin-android pack: bad fixture fails, good fixture passes"
