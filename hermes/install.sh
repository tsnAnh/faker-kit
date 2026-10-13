#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh --ref COMMIT [--source URL_OR_DIR] [--bootstrap-bot NAME] [--model M] [--claude-subscription] [--no-gateway]

Install the faker-kit Hermes module on this machine, pinned to one kit commit. Run it again
with a new commit to move every bot to it; it keeps guard.yaml, bots.yaml, job state, and
anything in a profile it does not own. The team-kit-* commands remain, as the same commands
as faker-kit-*.

  --ref COMMIT           Full 40-character kit commit. Each repository's .agents/dispatch.json
                         kit_ref must equal it before a project bot launches work there.
  --source SRC           Kit repository (default: https://github.com/tsnAnh/faker-kit.git)
  --bootstrap-bot NAME   Create or refresh the bootstrap bot, the one that sets up projects.
                         Any Hermes profile name: lowercase letters, digits, - and _.
  --model M              Model for the bootstrap bot (only with --bootstrap-bot)
  --claude-subscription  Run the bootstrap bot's model on your Claude subscription through the
                         logged-in Claude Code CLI (only with --bootstrap-bot)
  --no-gateway           Do not install or restart the default profile's shared gateway

Project bots are not created here. Ask the bootstrap bot for a new project; it runs
team-kit-admin add-project, which you approve.

It writes, under ~/.hermes:
  team-kit/kit/            the kit at COMMIT; every bot's files come from here
  team-kit/guard.yaml      the policy, written once from hermes/templates/guard.yaml
  team-kit/bots.yaml       each bot's role, project, and repositories; only team-kit-admin writes it
  bin/faker-kit-gh         a project bot's only GitHub command
  bin/faker-kit-admin      the bootstrap bot's only command
  bin/faker-kit-chief      the chief of staff's only command; reads, and proposes with approval
  bin/faker-kit-slack      the outbound-only Slack Socket Mode relay command
  bin/team-kit-gh, bin/team-kit-admin, bin/team-kit-chief, bin/team-kit-slack
                           the same four commands under their earlier names
  profiles/<bot>/          for each bot in bots.yaml: SOUL.md, config, the guard plugin, its skill

It stops before changing anything if a file it did not write is at one of the bin/ paths.
USAGE
}

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

wrapper_prefixes='faker-kit team-kit'
wrapper_names='gh admin chief slack'

# A kit wrapper is what the wrapper loop below writes, or wrote in an older kit: a regular file whose
# second line sets TEAM_KIT_HOME and whose third line execs the kit's hermes/bin/team_kit_<name>.py.
# Older kits quoted with printf %q, which leaves the script path bare, backslash-escaped, or, for a
# non-ASCII path without a UTF-8 locale, in $'...'; this one single-quotes it.
is_kit_wrapper() {
  local path=$1 name=$2 first second third
  [ -f "$path" ] && [ ! -L "$path" ] || return 1
  { IFS= read -r first && IFS= read -r second && IFS= read -r third; } < "$path" || return 1
  [[ $second == 'export TEAM_KIT_HOME='* && $third == 'exec '* ]] || return 1
  [[ $third == *"/hermes/bin/team_kit_$name.py \"\$@\"" || $third == *"/hermes/bin/team_kit_$name.py' \"\$@\"" ]]
}

# Single quotes give the same bytes under every locale. printf %q does not: launchd and cron run
# without LANG, and there it writes $'...' for a non-ASCII path.
shell_quote() {
  local s=$1 sq="'" q="'\\''"
  printf "'%s'" "${s//$sq/$q}"
}

ref='' source=https://github.com/tsnAnh/faker-kit.git bootstrap_bot='' model='' claude_subscription='' gateway=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref=$2; shift 2 ;;
    --source) source=$2; shift 2 ;;
    --bootstrap-bot) bootstrap_bot=$2; shift 2 ;;
    --model) model=$2; shift 2 ;;
    --claude-subscription) claude_subscription=1; shift ;;
    --no-gateway) gateway=0; shift ;;
    --dispatcher-model|--launcher-model)
      die "$1 is from version 1. Version 2 has no dispatcher or launcher; see hermes/README.md, Migrate from version 1" ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument $1. Run --help for usage." ;;
  esac
done
[[ $ref =~ ^[0-9a-f]{40}$ ]] || die "--ref must be a full 40-character commit, the same one as kit_ref"
[ -z "$model" ] || [ -n "$bootstrap_bot" ] || die "--model needs --bootstrap-bot"
[ -z "$claude_subscription" ] || [ -n "$bootstrap_bot" ] || die "--claude-subscription needs --bootstrap-bot"
command -v hermes >/dev/null || die "hermes is not on PATH; install Hermes Agent first"

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# team-kit-admin puts bin/ and profiles/ beside the team-kit directory, so a TEAM_KIT_HOME
# elsewhere (a throwaway test install) moves them too and never touches the real ~/.hermes.
kit_home=${TEAM_KIT_HOME:-$HOME/.hermes/team-kit}
root=$(dirname "$kit_home")
kit=$kit_home/kit

for prefix in $wrapper_prefixes; do
  for name in $wrapper_names; do
    path=$root/bin/$prefix-$name
    if [ -e "$path" ] || [ -L "$path" ]; then
      is_kit_wrapper "$path" "$name" || die "$path exists and is not a kit wrapper; move it aside, then run install.sh again"
    fi
  done
done

# A managed Hermes launcher may be a shell wrapper, not a venv shebang.
# These helpers only need Python + PyYAML; never run the shell launcher as Python
# or assume a managed base Python can import packages without its bootstrap.
usable_python() {
  [ -n "$1" ] && "$1" -c 'import sys, yaml; assert sys.version_info >= (3, 9)' >/dev/null 2>&1
}
py=''
if [ "${HERMES_PYTHON+x}" = x ]; then
  py=$HERMES_PYTHON
  usable_python "$py" || die "HERMES_PYTHON must name an executable Python 3.9+ with PyYAML installed"
else
  IFS= read -r shebang < "$(command -v hermes)" || true
  case $shebang in
    '#!'/*python*) candidate=${shebang#\#!} ;;
    '#!/usr/bin/env python3') candidate=$(command -v python3 || true) ;;
    *) candidate='' ;;
  esac
  if usable_python "$candidate"; then
    py=$candidate
  else
    candidate=$(command -v python3 || true)
    usable_python "$candidate" || die "No usable Python 3.9+ with PyYAML; install PyYAML in a helper Python and set HERMES_PYTHON to its executable path"
    py=$candidate
  fi
fi

mkdir -p "$kit_home" "$root/bin"
chmod 700 "$kit_home"
bash "$here/../plugins/trust-ladder/skills/setup-cloud-agents/scripts/install-kit-skills.sh" \
  --ref "$ref" --source "$source" --checkout "$kit" --dest "$kit_home/all-skills" >/dev/null
[ "$(git -C "$kit" rev-parse HEAD)" = "$ref" ] || die "kit checkout is not at $ref"
pkg=$kit/hermes
[ -f "$pkg/bin/team_kit_admin.py" ] || die "kit $ref has no hermes/ module; pin a newer commit"

if [ ! -f "$kit_home/guard.yaml" ]; then
  install -m 600 "$pkg/templates/guard.yaml" "$kit_home/guard.yaml"
  printf 'wrote %s; set trusted_authors to your GitHub login\n' "$kit_home/guard.yaml"
fi
if grep -Eq '^version:[[:space:]]*1[[:space:]]*$' "$kit_home/guard.yaml"; then
  die "$kit_home/guard.yaml is version 1; see hermes/README.md, Migrate from version 1"
fi

# The wrappers run the pinned kit's commands with the helper Python. A wrapper replaces the old
# one in one rename, so a bot never runs a half-written file.
for prefix in $wrapper_prefixes; do
  for name in $wrapper_names; do
    path=$root/bin/$prefix-$name
    printf '#!/usr/bin/env bash\nexport TEAM_KIT_HOME=%s\nexec %s %s "$@"\n' \
      "$(shell_quote "$kit_home")" "$(shell_quote "$py")" "$(shell_quote "$pkg/bin/team_kit_$name.py")" > "$path.tmp.$$"
    chmod 755 "$path.tmp.$$"
    mv -f "$path.tmp.$$" "$path"
  done
done

export TEAM_KIT_HOME=$kit_home
"$py" "$pkg/bin/team_kit_admin.py" sync >/dev/null
if [ -n "$bootstrap_bot" ]; then
  "$py" "$pkg/bin/team_kit_admin.py" add-bootstrap --bot "$bootstrap_bot" ${model:+--model "$model"} ${claude_subscription:+--claude-subscription} >/dev/null
  printf 'bootstrap bot %s ready; add it in Hermes Desktop Bot Mode and ask it for a project\n' "$bootstrap_bot"
fi

if [ "$gateway" = 1 ]; then
  hermes -p default gateway install >/dev/null
  hermes -p default gateway restart >/dev/null 2>&1 || hermes -p default gateway start >/dev/null
fi

printf 'faker-kit Hermes module installed at kit %s. The team-kit-* commands remain, as the same commands.\n' "$ref"
printf 'Each repository'"'"'s .agents/dispatch.json kit_ref must be %s; the bootstrap bot writes it for new repositories.\n' "$ref"
