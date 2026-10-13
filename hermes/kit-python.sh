#!/usr/bin/env bash
# The kit's own Python: a virtual environment with the pinned PyYAML, so an upgrade of the system or Homebrew
# python3 never breaks the team-kit commands. install.sh builds it; every team-kit-* wrapper runs it and calls
# `ensure` again when it no longer imports yaml, which rebuilds it from whatever Python is installed now.
#
#   kit-python.sh find-base [PYTHON]   print a Python 3.9+ that can build a virtual environment
#   kit-python.sh ensure VENV [PYTHON] make VENV such an environment, unless it already works
#
# Progress and errors go to stderr only: a wrapper's stdout is a scheduled job's delivered output.
set -euo pipefail

PYYAML='PyYAML==6.0.3'
LOCK_WAIT_SECONDS=300
STALE_LOCK_MINUTES=10

die() { printf 'kit-python.sh: %s\n' "$*" >&2; exit 1; }

can_build() {
  [ -n "$1" ] && "$1" -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 9)' >/dev/null 2>&1
}

works() {
  [ -x "$1/bin/python" ] && "$1/bin/python" -c 'import sys, yaml; assert sys.version_info >= (3, 9)' >/dev/null 2>&1
}

find_base() {
  if [ -n "${1:-}" ]; then
    can_build "$1" || die "$1 is not a Python 3.9+ that can build a virtual environment (venv and ensurepip)"
    printf '%s\n' "$1"
    return
  fi
  local name found
  for name in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9; do
    found=$(command -v "$name" 2>/dev/null) || continue
    if can_build "$found"; then
      printf '%s\n' "$found"
      return
    fi
  done
  die "no Python 3.9+ that can build a virtual environment; install one (on Debian: apt install python3-venv) or name it in HERMES_PYTHON"
}

# Script-level, because the EXIT trap that cleans them up runs after ensure returns. lock is set only once
# this run holds it, so a run that gave up waiting never removes another run's lock.
lock=''
tmp=''
cleanup() {
  [ -z "$tmp" ] || rm -rf "$tmp"
  [ -z "$lock" ] || rmdir "$lock" 2>/dev/null || true
}
trap cleanup EXIT

ensure() {
  local venv=$1 base want waited=0
  works "$venv" && return 0
  base=$(find_base "${2:-}")
  want=$venv.lock
  mkdir -p "$(dirname "$venv")"
  until mkdir "$want" 2>/dev/null; do
    if [ -n "$(find "$want" -maxdepth 0 -mmin +"$STALE_LOCK_MINUTES" 2>/dev/null)" ]; then
      rmdir "$want" 2>/dev/null || true
      continue
    fi
    [ "$waited" -lt "$LOCK_WAIT_SECONDS" ] || die "another build of $venv has held $want for ${LOCK_WAIT_SECONDS}s"
    sleep 1
    waited=$((waited + 1))
  done
  lock=$want
  tmp=$venv.new.$$
  # Another wrapper may have finished the build while this one waited for the lock.
  works "$venv" && return 0
  printf 'kit-python.sh: building %s from %s\n' "$venv" "$base" >&2
  rm -rf "$tmp"
  "$base" -m venv "$tmp" >&2 || die "$base could not create a virtual environment at $tmp"
  "$tmp/bin/python" -m pip install --quiet --disable-pip-version-check --no-input "$PYYAML" >&2 \
    || die "could not install $PYYAML into $tmp; check the network or pip's configuration"
  works "$tmp" || die "the new environment at $tmp cannot import yaml"
  rm -rf "$venv"
  mv "$tmp" "$venv"
  tmp=''
}

case ${1:-} in
  find-base) find_base "${2:-}" ;;
  ensure) [ -n "${2:-}" ] || die "usage: kit-python.sh ensure VENV [PYTHON]"; ensure "$2" "${3:-}" ;;
  *) die "usage: kit-python.sh find-base [PYTHON] | ensure VENV [PYTHON]" ;;
esac
