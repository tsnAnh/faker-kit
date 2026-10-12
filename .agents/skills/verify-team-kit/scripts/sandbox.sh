#!/usr/bin/env bash
# Drive the team-kit Hermes module for real, in an isolated temporary home.
# Hermes and GitHub are the external systems: the repo's own hermetic doubles
# (hermes/tests/fake_hermes.py, fake_gh.py) stand in for them on PATH. Every
# kit file, install step, and command is the real one, from the committed HEAD.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: .agents/skills/verify-team-kit/scripts/sandbox.sh <command> [args]

  up                 Install this checkout's HEAD into a new sandbox and print its directory.
  doctor             Is the sandbox worth driving? Prints the kit commit, bots, and checks the tools.
  run <cmd...>       Run a command inside the sandbox (its HOME, PATH, and TEAM_KIT_HOME).
  evidence <name> <cmd...>
                     Like run, and keep the full transcript and exit code as evidence/<name>.txt.
  down               Remove the sandbox. Evidence stays in .verify-evidence/<run>/.

The sandbox directory is kept in .verify-evidence/current; up refuses if one is already up.
USAGE
}

root=$(git rev-parse --show-toplevel)
evidence_root="$root/.verify-evidence"
pointer="$evidence_root/current"

sandbox() {
  [ -f "$pointer" ] || { echo "sandbox: none is up; run: sandbox.sh up" >&2; exit 2; }
  cat "$pointer"
}

in_sandbox() {
  local dir; dir=$(sandbox)
  # shellcheck disable=SC1091
  ( . "$dir/env.sh" && cd "$dir" && "$@" )
}

case "${1:-}" in
  up)
    [ -f "$pointer" ] && { echo "sandbox: $(cat "$pointer") is already up; run: sandbox.sh down" >&2; exit 2; }
    git -C "$root" diff --quiet HEAD -- hermes || echo "sandbox: note: uncommitted changes under hermes/ are not installed; it installs HEAD" >&2
    run_id=$(date -u +%Y%m%dT%H%M%SZ)
    dir=$(mktemp -d "${TMPDIR:-/tmp}/verify-team-kit.XXXXXX")
    mkdir -p "$dir/bin" "$dir/home/.hermes" "$evidence_root/$run_id"
    py=$(command -v python3)
    printf '#!%s\n' "$py" | cat - "$root/hermes/tests/fake_hermes.py" > "$dir/bin/hermes"
    printf '#!%s\n' "$py" | cat - "$root/hermes/tests/fake_gh.py" > "$dir/bin/gh"
    chmod +x "$dir/bin/hermes" "$dir/bin/gh"
    cat > "$dir/env.sh" <<ENV
export HOME="$dir/home"
export PATH="$dir/bin:/usr/bin:/bin:$(dirname "$py")"
export TMPDIR="$dir"
export TEAM_KIT_SOURCE_URL="$root"
export VERIFY_EVIDENCE="$evidence_root/$run_id"
unset CLAUDE_CONFIG_DIR GH_TOKEN GITHUB_TOKEN HERMES_HOME TEAM_KIT_HOME
ENV
    echo "$dir" > "$pointer"
    echo "$run_id" > "$dir/run-id"
    sha=$(git -C "$root" rev-parse HEAD)
    if ! in_sandbox bash "$root/hermes/install.sh" --ref "$sha" --source "$root" --no-gateway > "$evidence_root/$run_id/install.txt" 2>&1; then
      echo "sandbox: install.sh failed; see $evidence_root/$run_id/install.txt" >&2; exit 1
    fi
    python3 - "$dir/home/.hermes/team-kit/guard.yaml" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
open(p, "w").write(s.replace("  - OWNER-LOGIN\n", "  - verify-owner\n  - verify-bot\n"))
PY
    echo "$dir"
    ;;
  doctor)
    dir=$(sandbox)
    in_sandbox bash -c '
      set -e
      test -x "$HOME/.hermes/bin/team-kit-admin" || { echo "doctor: team-kit-admin is not installed"; exit 1; }
      echo "kit: $(git -C "$HOME/.hermes/team-kit/kit" rev-parse --short HEAD)"
      echo "hermes: $(command -v hermes) (the hermetic double)"
      echo "gh: $(command -v gh) (the hermetic double)"
      "$HOME/.hermes/bin/team-kit-admin" status
    '
    ;;
  run)
    shift; in_sandbox "$@"
    ;;
  evidence)
    shift; name=${1:?evidence needs a name}; shift
    dir=$(sandbox); run_id=$(cat "$dir/run-id"); out="$evidence_root/$run_id/$name.txt"
    { printf '$ %s\n' "$*"; in_sandbox "$@" 2>&1; status=$?; printf '\n[exit %s]\n' "$status"; } > "$out" || true
    cat "$out"
    ;;
  down)
    dir=$(sandbox); run_id=$(cat "$dir/run-id")
    rm -rf "$dir" "$pointer"
    echo "sandbox removed; evidence kept in $evidence_root/$run_id"
    ;;
  -h|--help|"") usage ;;
  *) usage >&2; exit 2 ;;
esac
