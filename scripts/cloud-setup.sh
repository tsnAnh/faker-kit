#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/cloud-setup.sh

Install what the kit's checks need (see AGENTS.md, Checks) in a fresh Ubuntu or Debian container, such as a
Claude Code cloud session: Node 22 or later, Bun, jq, git, Python 3 with venv, and
ruff, mypy, types-PyYAML and pyyaml in a tool venv linked onto PATH. Running it again
changes nothing that is already installed. It reads no secrets and writes none.
USAGE
}

[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && { usage; exit 0; }

NODE_MAJOR=22
BUN_VERSION=1.4.4
TOOLS_VENV=/opt/faker-kit-tools
PY_TOOLS=(ruff mypy types-PyYAML pyyaml)

say() { printf 'cloud-setup: %s\n' "$*"; }
die() { printf 'cloud-setup: %s\n' "$*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
  as_root() { "$@"; }
elif command -v sudo >/dev/null 2>&1; then
  as_root() { sudo "$@"; }
else
  die "needs root or sudo to install packages"
fi

command -v apt-get >/dev/null 2>&1 || die "needs apt-get (Ubuntu or Debian)"

apt_updated=false
apt_install() {
  local missing=()
  for package in "$@"; do
    dpkg -s "$package" >/dev/null 2>&1 || missing+=("$package")
  done
  [ ${#missing[@]} -eq 0 ] && return 0
  if [ "$apt_updated" = false ]; then
    as_root apt-get update -qq
    apt_updated=true
  fi
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${missing[@]}" >/dev/null
  say "installed ${missing[*]}"
}

node_major() {
  command -v node >/dev/null 2>&1 || { echo 0; return; }
  node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0
}

apt_install ca-certificates curl git jq unzip python3 python3-venv

if [ "$(node_major)" -lt "$NODE_MAJOR" ]; then
  apt_install gnupg
  as_root install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | as_root gpg --batch --yes --dearmor -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" | as_root tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  as_root apt-get update -qq
  apt_updated=true
  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends nodejs >/dev/null
  [ "$(node_major)" -ge "$NODE_MAJOR" ] || die "node $NODE_MAJOR or later did not install"
  say "installed node $(node --version)"
fi

if ! command -v bun >/dev/null 2>&1; then
  case "$(uname -m)" in
    x86_64 | amd64) bun_arch=x64 bun_sha256=bf8ccbfb2b0b80159e0f9a6d15c55317add8077534dbc6130a3edb52fccb395c ;;
    aarch64 | arm64) bun_arch=aarch64 bun_sha256=baa6bb36f51d6e58561e02bbea825038a9df454f673d6d776e8bbf86f8c7850e ;;
    *) die "no pinned bun build for $(uname -m)" ;;
  esac
  bun_tmp=$(mktemp -d)
  trap 'rm -rf "$bun_tmp"' EXIT
  curl -fsSL -o "$bun_tmp/bun.zip" "https://github.com/oven-sh/bun/releases/download/bun-v${BUN_VERSION}/bun-linux-${bun_arch}.zip"
  echo "$bun_sha256  $bun_tmp/bun.zip" | sha256sum -c --quiet - || die "bun ${BUN_VERSION} archive failed its checksum"
  unzip -q "$bun_tmp/bun.zip" -d "$bun_tmp"
  as_root install -m 0755 "$bun_tmp/bun-linux-${bun_arch}/bun" /usr/local/bin/bun
  rm -rf "$bun_tmp"
  trap - EXIT
  command -v bun >/dev/null 2>&1 || die "bun did not install"
  say "installed bun $(bun --version) (pinned ${BUN_VERSION}, checksum verified)"
fi

if [ ! -x "$TOOLS_VENV/bin/python" ]; then
  as_root python3 -m venv "$TOOLS_VENV"
  say "created $TOOLS_VENV"
fi
if ! as_root "$TOOLS_VENV/bin/python" -m pip show "${PY_TOOLS[@]}" >/dev/null 2>&1; then
  as_root "$TOOLS_VENV/bin/python" -m pip install --quiet --disable-pip-version-check "${PY_TOOLS[@]}"
  say "installed ${PY_TOOLS[*]}"
fi
for tool in ruff mypy; do
  [ "$(readlink -f "/usr/local/bin/$tool" 2>/dev/null)" = "$TOOLS_VENV/bin/$tool" ] || as_root ln -sf "$TOOLS_VENV/bin/$tool" "/usr/local/bin/$tool"
done
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
  apt_install python3-yaml
fi

say "node $(node --version), bun $(bun --version), jq $(jq --version), $(git --version), $(python3 --version)"
say "ruff $(ruff --version | cut -d' ' -f2), mypy $(mypy --version | cut -d' ' -f2), claude $(claude --version 2>/dev/null | cut -d' ' -f1 || echo 'not installed')"
say "ready: run the checks AGENTS.md lists for your change"
