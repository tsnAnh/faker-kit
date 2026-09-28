#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
detekt=$("$pack/../tools.sh" detekt)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
configs="$pack/../kotlin-android/files/config/detekt/detekt.yml,$pack/files/config/detekt/detekt-kmp.yml"

if java -jar "$detekt" --build-upon-default-config --config "$configs" --input "$pack/fixtures/bad" > "$work/bad.log" 2>&1; then
  echo "kmp pack: detekt passed the bad fixture" >&2; exit 1
fi
for needle in "android.util.Log" "java.io.File"; do
  grep "/commonMain/" "$work/bad.log" | grep -q "$needle" || { echo "kmp pack: detekt did not flag $needle in commonMain" >&2; cat "$work/bad.log" >&2; exit 1; }
done
if grep -q "/androidMain/.*ForbiddenImport" "$work/bad.log"; then
  echo "kmp pack: detekt flagged an import in androidMain, where platform APIs belong" >&2; cat "$work/bad.log" >&2; exit 1
fi
java -jar "$detekt" --build-upon-default-config --config "$configs" --input "$pack/fixtures/good" > "$work/good.log" 2>&1 || { echo "kmp pack: detekt failed the good fixture" >&2; cat "$work/good.log" >&2; exit 1; }
echo "kmp pack: bad fixture fails, good fixture passes"
