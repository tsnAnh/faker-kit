#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh --ref COMMIT [--source URL_OR_DIR] [--bootstrap-bot NAME] [--provider P --model M] [--claude-subscription] [--no-gateway]

Install the faker-kit Hermes module on this machine, pinned to one kit commit. Run it again
with a new commit to move every bot to it; it keeps guard.yaml, bots.yaml, job state, and
anything in a profile it does not own.

  --ref COMMIT           Full 40-character kit commit. Each repository's vendored kit must be this
                         commit before a project bot launches work there.
  --source SRC           Kit repository (default: https://github.com/tsnAnh/faker-kit.git)
  --bootstrap-bot NAME   Create or refresh the bootstrap bot, the one that sets up projects.
                         Any Hermes profile name: lowercase letters, digits, - and _.
  --model M              Model for the bootstrap bot (only with --bootstrap-bot)
  --provider P           Hermes provider id for the bootstrap bot, given together with --model
                         (only with --bootstrap-bot; not with --claude-subscription)
  --claude-subscription  Run the bootstrap bot's model on your Claude subscription through the
                         logged-in Claude Code CLI (only with --bootstrap-bot)
  --no-gateway           Do not install or restart the default profile's shared gateway

Project bots are not created here. Ask the bootstrap bot for a new project; it runs
faker-kit add-project, which you approve.

When ~/.hermes/faker-kit/machine.yaml exists (faker-kit install --config keeps a copy there), it also
runs faker-kit install --config on it, which refreshes the bots, the guard settings, and the GitHub
webhook routes, and faker-kit runner up --config on it, which starts the CI runners.

It writes, under ~/.hermes:
  faker-kit/kit/            the kit at COMMIT; every bot's files come from here
  faker-kit/guard.yaml      the policy, written once from hermes/templates/guard.yaml
  faker-kit/bots.yaml       each bot's role, project, and repository; only faker-kit writes it
  bin/faker-kit             the one command every bot and the owner run
  profiles/<bot>/           for each bot in bots.yaml: SOUL.md, config, the guard plugin, its skill

It stops before changing anything if a file it did not write is at one of the bin/ paths.
USAGE
}

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

exec_line='^exec .*faker_kit\.py'"'"'? "[$]@"$'

is_kit_wrapper() {
  local path=$1 line n=0 second='' found=''
  [ -f "$path" ] && [ ! -L "$path" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    [ "$n" = 2 ] && second=$line
    [[ $line =~ $exec_line ]] && found=1
  done < "$path"
  [[ $second == 'export FAKER_KIT_HOME='* && -n $found ]]
}

shell_quote() {
  local s=$1 sq="'" q="'\\''"
  printf "'%s'" "${s//$sq/$q}"
}

ref='' source=https://github.com/tsnAnh/faker-kit.git bootstrap_bot='' model='' provider='' claude_subscription='' gateway=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref=$2; shift 2 ;;
    --source) source=$2; shift 2 ;;
    --bootstrap-bot) bootstrap_bot=$2; shift 2 ;;
    --model) model=$2; shift 2 ;;
    --provider) provider=$2; shift 2 ;;
    --claude-subscription) claude_subscription=1; shift ;;
    --no-gateway) gateway=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument $1. Run --help for usage." ;;
  esac
done
[[ $ref =~ ^[0-9a-f]{40}$ ]] || die "--ref must be a full 40-character commit"
[ -z "$model" ] || [ -n "$bootstrap_bot" ] || die "--model needs --bootstrap-bot"
[ -z "$claude_subscription" ] || [ -n "$bootstrap_bot" ] || die "--claude-subscription needs --bootstrap-bot"
[ -z "$provider" ] || [ -n "$bootstrap_bot" ] || die "--provider needs --bootstrap-bot"
command -v hermes >/dev/null || die "hermes is not on PATH; install Hermes Agent first"

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
kit_home=${FAKER_KIT_HOME:-$HOME/.hermes/faker-kit}
root=$(dirname "$kit_home")
kit=$kit_home/kit

path=$root/bin/faker-kit
if { [ -e "$path" ] || [ -L "$path" ]; } && ! is_kit_wrapper "$path"; then
  die "$path exists and is not a kit wrapper; move it aside, then run install.sh again"
fi

usable_python() {
  [ -n "$1" ] && "$1" -c 'import sys; assert sys.version_info >= (3, 9)' >/dev/null 2>&1
}
py=''
if [ "${HERMES_PYTHON+x}" = x ]; then
  py=$HERMES_PYTHON
  usable_python "$py" || die "HERMES_PYTHON must name an executable Python 3.9+"
else
  candidate=$(command -v python3 || true)
  if usable_python "$candidate"; then
    py=$candidate
  else
    IFS= read -r shebang < "$(command -v hermes)" || true
    case $shebang in
      '#!'/*python*) candidate=${shebang#\#!} ;;
      *) candidate='' ;;
    esac
    usable_python "$candidate" || die "No usable Python 3.9+; install one or set HERMES_PYTHON to its executable path"
    py=$candidate
  fi
fi

mkdir -p "$root/bin"
mkdir -p "$kit_home"
chmod 700 "$kit_home"
bash "$here/../plugins/trust-ladder/skills/setup-cloud-agents/scripts/install-kit-skills.sh" \
  --ref "$ref" --source "$source" --checkout "$kit" --dest "$kit_home/all-skills" >/dev/null
[ "$(git -C "$kit" rev-parse HEAD)" = "$ref" ] || die "kit checkout is not at $ref"
pkg=$kit/hermes
[ -f "$pkg/bin/faker_kit.py" ] || die "kit $ref has no hermes/bin/faker_kit.py; pin a newer commit"

if [ ! -f "$kit_home/guard.yaml" ]; then
  install -m 600 "$pkg/templates/guard.yaml" "$kit_home/guard.yaml"
  printf 'wrote %s; set trusted_authors to your GitHub login\n' "$kit_home/guard.yaml"
fi

printf '#!/usr/bin/env bash\nexport FAKER_KIT_HOME=%s\npy=%s\n[ -x "$py" ] || py=$(command -v python3) || { echo "faker-kit: no python3 to run it" >&2; exit 1; }\nexec "$py" %s "$@"\n' \
  "$(shell_quote "$kit_home")" "$(shell_quote "$py")" "$(shell_quote "$pkg/bin/faker_kit.py")" > "$path.tmp.$$"
chmod 755 "$path.tmp.$$"
mv -f "$path.tmp.$$" "$path"

export FAKER_KIT_HOME=$kit_home
unset HERMES_HOME
machine=$kit_home/machine.yaml
runner_failed=''
if [ -f "$machine" ]; then
  "$py" "$pkg/bin/faker_kit.py" install --config "$machine" ${bootstrap_bot:+--bootstrap-bot "$bootstrap_bot"} ${model:+--model "$model"} ${provider:+--provider "$provider"} ${claude_subscription:+--claude-subscription} >/dev/null \
    || die "faker-kit install --config $machine failed; fix the config, then run install.sh again"
  if "$py" "$pkg/bin/faker_kit.py" runner up --config "$machine" >/dev/null; then
    printf 'refreshed the bots, webhook routes, and CI runner services from %s\n' "$machine"
  else
    runner_failed=1
    printf 'install.sh: faker-kit runner up --config %s failed; the CI runner services are not refreshed (is Docker running?)\n' "$machine" >&2
  fi
else
  "$py" "$pkg/bin/faker_kit.py" install ${bootstrap_bot:+--bootstrap-bot "$bootstrap_bot"} ${model:+--model "$model"} ${provider:+--provider "$provider"} ${claude_subscription:+--claude-subscription} >/dev/null
fi
[ -z "$bootstrap_bot" ] || printf 'bootstrap bot %s ready; add it in Hermes Desktop Bot Mode and ask it for a project\n' "$bootstrap_bot"

if [ "$gateway" = 1 ]; then
  hermes -p default gateway install >/dev/null
  hermes -p default gateway restart >/dev/null 2>&1 || hermes -p default gateway start >/dev/null
fi

printf 'faker-kit Hermes module installed at kit %s.\n' "$ref"
[ -z "$runner_failed" ] || die "the CI runner services were not refreshed; fix the cause, then run install.sh again"
printf 'Each repository'"'"'s vendored kit must be %s; the bootstrap bot runs faker-kit vendor to move it.\n' "$ref"
