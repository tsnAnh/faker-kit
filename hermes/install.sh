#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh --ref COMMIT [--source URL_OR_DIR] [--bootstrap-bot NAME] [--model M] [--claude-subscription] [--no-gateway]

Install the team-kit Hermes module on this machine, pinned to one kit commit. Run it again
with a new commit to move every bot to it; it keeps guard.yaml, bots.yaml, job state, and
anything in a profile it does not own.

  --ref COMMIT           Full 40-character kit commit. Each repository's .agents/dispatch.json
                         kit_ref must equal it before a project bot launches work there.
  --source SRC           Kit repository (default: https://github.com/tsnAnh/team-kit.git)
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
  bin/team-kit-gh          a project bot's only GitHub command
  bin/team-kit-admin       the bootstrap bot's only command
  bin/team-kit-chief       the chief of staff's only command; reads, and proposes with approval
  bin/team-kit-slack       the outbound-only Slack Socket Mode relay command
  profiles/<bot>/          for each bot in bots.yaml: SOUL.md, config, the guard plugin, its skill
USAGE
}

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

ref='' source=https://github.com/tsnAnh/team-kit.git bootstrap_bot='' model='' claude_subscription='' gateway=1
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

# The commands run in the kit's own virtual environment ($kit_home/python, PyYAML pinned), never in the
# system or Homebrew python3, so upgrading that Python cannot break them. HERMES_PYTHON names the Python to
# build it from; otherwise the first python3 that can build one is used. Checked before anything is written.
if [ "${HERMES_PYTHON+x}" = x ]; then
  base=$(bash "$here/kit-python.sh" find-base "$HERMES_PYTHON" 2>&1) \
    || die "HERMES_PYTHON must name an executable Python 3.9+ that can build a virtual environment: ${base#kit-python.sh: }"
else
  base=$(bash "$here/kit-python.sh" find-base 2>&1) || die "${base#kit-python.sh: }"
fi

mkdir -p "$kit_home" "$root/bin"
chmod 700 "$kit_home"
bash "$here/../plugins/trust-ladder/skills/setup-cloud-agents/scripts/install-kit-skills.sh" \
  --ref "$ref" --source "$source" --checkout "$kit" --dest "$kit_home/all-skills" >/dev/null
[ "$(git -C "$kit" rev-parse HEAD)" = "$ref" ] || die "kit checkout is not at $ref"
pkg=$kit/hermes
[ -f "$pkg/bin/team_kit_admin.py" ] || die "kit $ref has no hermes/ module; pin a newer commit"
install -m 755 "$here/kit-python.sh" "$kit_home/kit-python.sh"
bash "$kit_home/kit-python.sh" ensure "$kit_home/python" "$base" || die "could not build the kit's Python at $kit_home/python"
py=$kit_home/python/bin/python

if [ ! -f "$kit_home/guard.yaml" ]; then
  install -m 600 "$pkg/templates/guard.yaml" "$kit_home/guard.yaml"
  printf 'wrote %s; set trusted_authors to your GitHub login\n' "$kit_home/guard.yaml"
fi
if grep -Eq '^version:[[:space:]]*1[[:space:]]*$' "$kit_home/guard.yaml"; then
  die "$kit_home/guard.yaml is version 1; see hermes/README.md, Migrate from version 1"
fi

# The wrappers run the pinned kit's commands in the kit's Python, rebuilding it first if it no longer imports
# yaml. Rebuild output goes to stderr, so a scheduled job's stdout stays its notice. %q quotes paths that
# contain spaces or shell metacharacters.
for name in gh admin chief slack; do
  printf '#!/usr/bin/env bash\nexport TEAM_KIT_HOME=%q\n"$TEAM_KIT_HOME/python/bin/python" -c "import yaml" 2>/dev/null || bash "$TEAM_KIT_HOME/kit-python.sh" ensure "$TEAM_KIT_HOME/python" >&2 || exit 1\nexec "$TEAM_KIT_HOME/python/bin/python" %q "$@"\n' \
    "$kit_home" "$pkg/bin/team_kit_$name.py" > "$root/bin/team-kit-$name"
  chmod 755 "$root/bin/team-kit-$name"
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

printf 'team-kit Hermes module installed at kit %s.\n' "$ref"
printf 'Each repository'"'"'s .agents/dispatch.json kit_ref must be %s; the bootstrap bot writes it for new repositories.\n' "$ref"
