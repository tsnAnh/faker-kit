#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: packs/tools.sh <detekt|ktlint|swiftlint>

Download a pinned linter into ${FAKER_KIT_TOOLS:-~/.cache/faker-kit-tools}, verify its
SHA-256, and print the path to run. Pack tests and CI use this so everyone runs the
same version.

Example:
  java -jar "$(packs/tools.sh detekt)" --version
USAGE
}

cache=${FAKER_KIT_TOOLS:-${XDG_CACHE_HOME:-$HOME/.cache}/faker-kit-tools}
case "${1:-}" in
  detekt)
    url=https://github.com/detekt/detekt/releases/download/v1.23.8/detekt-cli-1.23.8-all.jar
    sha=2ce2ff952e150baf28a29cda70a363b0340b3e81a55f43e51ec5edffc3d066c1
    file=$cache/detekt-cli-1.23.8-all.jar ;;
  ktlint)
    url=https://github.com/pinterest/ktlint/releases/download/1.5.0/ktlint
    sha=a16be01dcc480aab2f55f444b620142152f66e31564b3b9376506d624c28a2ad
    file=$cache/ktlint-1.5.0 ;;
  swiftlint)
    case "$(uname -m)" in
      aarch64|arm64) arch=arm64; sha=9ffa52f478e6d8eb485d37d14715ffac90abc81c58f3370d598bf75be05605f8 ;;
      *) arch=amd64; sha=caeed6f4a679c35539ffaf124f6c4ab4a8416917f7d8796279dc52b74026059d ;;
    esac
    url=https://github.com/realm/SwiftLint/releases/download/0.65.1/swiftlint_linux_$arch.zip
    file=$cache/swiftlint-0.65.1-$arch.zip
    unpack=swiftlint-static ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

if [ ! -f "$file" ] || [ "$(sha256sum "$file" | cut -d' ' -f1)" != "$sha" ]; then
  mkdir -p "$cache"
  curl -sSfL -o "$file.tmp" "$url"
  actual=$(sha256sum "$file.tmp" | cut -d' ' -f1)
  if [ "$actual" != "$sha" ]; then
    rm -f "$file.tmp"
    printf 'tools.sh: %s has sha256 %s, expected %s\n' "$url" "$actual" "$sha" >&2
    exit 1
  fi
  mv "$file.tmp" "$file"
  chmod +x "$file"
fi
if [ "${unpack:-}" ]; then
  dir=${file%.zip}
  if [ ! -x "$dir/$unpack" ]; then
    rm -rf "$dir" && mkdir -p "$dir"
    unzip -q -o "$file" -d "$dir" 2>/dev/null || python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$file" "$dir"
    chmod +x "$dir/$unpack"
  fi
  file=$dir/$unpack
fi
printf '%s\n' "$file"
