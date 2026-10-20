#!/usr/bin/env bash
set -euo pipefail
pack=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

prepare() {
  local repo="$work/$1"
  mkdir -p "$repo/scripts"
  cp -R "$pack/fixtures/$1/repo/." "$repo/"
  cp -R "$pack/files/scripts/agent-checks" "$repo/scripts/agent-checks"
  git -C "$repo" init -q -b main
  git -C "$repo" add -A
  git -C "$repo" -c user.name=t -c user.email=t@t commit -qm base
  cp -R "$pack/fixtures/$1/pending/." "$repo/"
}

expect_fail() {
  local what=$1 needle=$2; shift 2
  if (cd "$work/bad" && "$@") > "$work/out.log" 2>&1; then echo "agnostic pack: $what passed on the bad fixture" >&2; exit 1; fi
  grep -q -- "$needle" "$work/out.log" || { echo "agnostic pack: $what did not report $needle" >&2; cat "$work/out.log" >&2; exit 1; }
}

expect_pass() {
  (cd "$work/good" && "$@") > "$work/out.log" 2>&1 || { echo "agnostic pack: $* failed on the good fixture" >&2; cat "$work/out.log" >&2; exit 1; }
}

prepare bad
prepare good

expect_fail "ratchet" "rust-unwrap: 3 found, baseline allows 2. Return a Result instead." node scripts/agent-checks/ratchet.mjs check
expect_fail "ratchet" "src/b.rs (0 -> 1)" node scripts/agent-checks/ratchet.mjs check
expect_fail "import-boundaries" "cart_page.dart:1: imports ../data/cart_api.dart \[ui-no-data\]" node scripts/agent-checks/import-boundaries.mjs
expect_fail "import-boundaries" "cart_page.dart:2: imports package:http/http.dart \[ui-no-data\]" node scripts/agent-checks/import-boundaries.mjs
expect_fail "import-boundaries" "app/domain/order.py:1: imports ..ui \\[domain-no-framework\\]" node scripts/agent-checks/import-boundaries.mjs
expect_fail "import-boundaries" "app/domain/order.py:2: imports django.db \\[domain-no-framework\\]" node scripts/agent-checks/import-boundaries.mjs
expect_fail "import-boundaries" "Store.kt:3: imports android.content.Context \\[common-no-android\\]" node scripts/agent-checks/import-boundaries.mjs
expect_fail "rule-fixtures" "passed on .agents/rule-fixtures/no-console/bad.ts; the rule no longer fires" node scripts/agent-checks/rule-fixtures.mjs
expect_fail "no-new-comments" "src/new.ts:2: // HACK: retry twice because the API is flaky" node scripts/agent-checks/no-new-comments.mjs --base HEAD
expect_fail "locale-keys" 'lib/l10n/app_vi.arb: missing "save"' node scripts/agent-checks/locale-keys.mjs
expect_fail "locale-keys" 'Localizable.strings: "count" has placeholders \[%@\] but the source has \[%d\]' node scripts/agent-checks/locale-keys.mjs

expect_pass node scripts/agent-checks/ratchet.mjs check
expect_pass node scripts/agent-checks/import-boundaries.mjs
expect_pass node scripts/agent-checks/rule-fixtures.mjs
expect_pass node scripts/agent-checks/no-new-comments.mjs --base HEAD
expect_pass node scripts/agent-checks/locale-keys.mjs
echo "agnostic pack: bad fixture fails, good fixture passes"
