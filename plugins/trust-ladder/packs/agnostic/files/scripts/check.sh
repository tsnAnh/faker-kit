#!/usr/bin/env bash
set -uo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/check.sh [--list]

Run every check this repository requires, the same way CI does. The commands live
in .agents/check-commands, one per line; lines starting with # are skipped. Each
lint pack installed by the team kit adds its lines there.

  --list   Print the commands without running them.

Exit code is 0 only when every command passes.
USAGE
}

root=$(git rev-parse --show-toplevel)
cd "$root"
commands_file=.agents/check-commands
[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && { usage; exit 0; }
[ -f "$commands_file" ] || { printf 'check: %s is missing. Run the setup-lint-pack skill to create it.\n' "$commands_file" >&2; exit 2; }

mapfile -t commands < <(grep -v -E '^\s*(#|$)' "$commands_file")
if [ "${1:-}" = "--list" ]; then printf '%s\n' "${commands[@]}"; exit 0; fi

failed=()
for command in "${commands[@]}"; do
  printf '\n==> %s\n' "$command"
  start=$SECONDS
  if bash -c "$command"; then
    printf '    ok (%ss)\n' $((SECONDS - start))
  else
    printf '    FAILED (%ss)\n' $((SECONDS - start))
    failed+=("$command")
  fi
done

printf '\n'
if [ ${#failed[@]} -gt 0 ]; then
  printf 'check: %d of %d failed:\n' "${#failed[@]}" "${#commands[@]}" >&2
  printf '  %s\n' "${failed[@]}" >&2
  exit 1
fi
printf 'check: all %d passed\n' "${#commands[@]}"
