#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

run_fixture() {
  local name=$1
  cp -R "$pack/fixtures/$name" "$work/$name"
  cat "$pack/merge/cargo-lints.toml" >> "$work/$name/Cargo.toml"
  cp "$pack/files/clippy.toml" "$work/$name/"
  (cd "$work/$name" && CARGO_TARGET_DIR="$work/target-$name" cargo clippy --quiet --all-targets -- -D warnings)
}

if run_fixture bad > "$work/bad.log" 2>&1; then
  echo "rust pack: bad fixture passed clippy; the lints are not firing" >&2
  exit 1
fi
for lint in unwrap_used expect_used dbg_macro print_stdout todo allow_attributes; do
  grep -q "index.html#$lint" "$work/bad.log" || { echo "rust pack: expected clippy::$lint on the bad fixture" >&2; cat "$work/bad.log" >&2; exit 1; }
done
run_fixture good
echo "rust pack: bad fixture fails, good fixture passes"
