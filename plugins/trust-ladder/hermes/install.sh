#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh --ref COMMIT [--source URL_OR_DIR] [--dispatcher-model M] [--launcher-model M] [--no-gateway]

Install the Hermes side of team-kit dispatch on this machine, pinned to one kit commit.
Run it again to move to a new commit; it keeps guard.yaml, the webhook secret, and
anything in the profiles it does not own.

  --ref COMMIT            Full 40-character kit commit. Must equal kit_ref in each repository's .agents/dispatch.json
  --source SRC            Kit repository (default: https://github.com/tsnAnh/team-kit.git)
  --dispatcher-model M    Model for the dispatcher profile (a strong one)
  --launcher-model M      Model for the launcher profile (a cheap one)
  --no-gateway            Do not install or restart the default profile's shared host gateway

New dispatcher jobs are created paused until repository allowlists, scoped
credentials, and the public webhook have been configured. Reinstalls leave
existing job state unchanged.

It writes, under ~/.hermes:
  config.yaml                     host webhook route targeting profile dispatcher
  team-kit/kit/                    the kit at COMMIT
  skills/, profiles/dispatcher/skills/
                                   links to the dispatch and setup-dispatch skills only
  profiles/{dispatcher,launcher}/  config merged, SOUL.md, the team_kit_guard plugin
  profiles/dispatcher/scripts/team-kit/
                                   github-triage.py, team_kit_contracts.py, team_kit_gh.py
  bin/team-kit-gh                  the dispatcher's only GitHub command
  team-kit/guard.yaml              sample policy, written once
  team-kit/webhook-secret          generated once, mode 600
USAGE
}

die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

ref='' source=https://github.com/tsnAnh/team-kit.git dispatcher_model='' launcher_model='' gateway=1
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref=$2; shift 2 ;;
    --source) source=$2; shift 2 ;;
    --dispatcher-model) dispatcher_model=$2; shift 2 ;;
    --launcher-model) launcher_model=$2; shift 2 ;;
    --no-gateway) gateway=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument $1. Run --help for usage." ;;
  esac
done
[[ $ref =~ ^[0-9a-f]{40}$ ]] || die "--ref must be a full 40-character commit, the same one as kit_ref"
command -v hermes >/dev/null || die "hermes is not on PATH; install Hermes Agent first"

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$HOME/.hermes
kit_home=$root/team-kit
kit=$kit_home/kit

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

bash "$here/../skills/setup-cloud-agents/scripts/install-kit-skills.sh" \
  --ref "$ref" --source "$source" --checkout "$kit" --dest "$kit_home/all-skills" >/dev/null
[ "$(git -C "$kit" rev-parse HEAD)" = "$ref" ] || die "kit checkout is not at $ref"
pkg=$kit/plugins/trust-ladder/hermes
skills=$kit/plugins/trust-ladder/skills
config_helper=$here/install_config.py

link_skill() {
  local name=$1 dest=$2 target=$2/$1
  mkdir -p "$dest"
  if [ -L "$target" ]; then
    rm "$target"
  elif [ -e "$target" ]; then
    printf 'conflict: %s is a real directory; left as is\n' "$target" >&2
    return
  fi
  ln -s "$skills/$name" "$target"
}

mkdir -p "$kit_home/briefs"
chmod 700 "$kit_home"

if [ ! -f "$kit_home/webhook-secret" ]; then
  (umask 077; "$py" -c 'import secrets; print(secrets.token_hex(32))' > "$kit_home/webhook-secret")
fi
# Refuse another owner's route before changing any Hermes profile configuration.
"$py" "$config_helper" webhook-check "$root" "$pkg/webhook/config.yaml"

if [ ! -f "$kit_home/guard.yaml" ]; then
  install -m 600 "$kit/plugins/trust-ladder/templates/dispatch/guard.yaml" "$kit_home/guard.yaml"
  printf 'wrote %s; fill in repos and trusted_authors\n' "$kit_home/guard.yaml"
fi

for profile in dispatcher launcher; do
  home=$root/profiles/$profile
  [ -d "$home" ] || hermes -p default profile create "$profile" >/dev/null
  [ -d "$home" ] || die "hermes profile create $profile did not create $home"
  "$py" "$config_helper" profile "$root" "$profile" "$pkg/profiles/$profile/config.yaml"
  if [ -f "$home/SOUL.md" ] && ! cmp -s "$home/SOUL.md" "$pkg/profiles/$profile/SOUL.md"; then
    cp "$home/SOUL.md" "$home/SOUL.md.bak"
  fi
  cp "$pkg/profiles/$profile/SOUL.md" "$home/SOUL.md"
  mkdir -p "$home/plugins"
  rm -rf "$home/plugins/team_kit_guard"
  cp -R "$pkg/plugin/team_kit_guard" "$home/plugins/team_kit_guard"
done

[ -z "$dispatcher_model" ] || hermes -p dispatcher config set model.default "$dispatcher_model" >/dev/null
[ -z "$launcher_model" ] || hermes -p launcher config set model.default "$launcher_model" >/dev/null

dispatcher=$root/profiles/dispatcher
"$py" "$config_helper" webhook "$root" "$pkg/webhook/config.yaml"
for dest in "$root/skills" "$dispatcher/skills"; do
  link_skill dispatch "$dest"
  link_skill setup-dispatch "$dest"
done

scripts=$dispatcher/scripts/team-kit
mkdir -p "$scripts" "$root/bin"
cp "$pkg/webhook/github-triage.py" "$pkg/webhook/team_kit_contracts.py" "$pkg/profiles/dispatcher/team_kit_gh.py" "$scripts/"
# Bash's %q quotes paths containing spaces or shell metacharacters for the wrapper.
printf '#!/usr/bin/env bash\nexec %q %q "$@"\n' "$py" "$scripts/team_kit_gh.py" > "$root/bin/team-kit-gh"
chmod 755 "$root/bin/team-kit-gh"

existing=$(hermes -p dispatcher cron list --all) || die "cannot list dispatcher cron jobs; refusing to create possible duplicates"
existing=$(printf '%s\n' "$existing" | "$py" "$config_helper" cron-names)
# Keep the Python parsing outside a process-substitution heredoc: Apple's Bash 3
# misparses backticks inside that combination, even with a quoted delimiter.
cron_spec=$("$py" "$config_helper" cron-spec "$pkg/cron/dispatcher-drain.md" "$kit_home")
while IFS=$'\t' read -r name schedule deliver skill prompt_file; do
  if grep -Fxq -- "$name" <<<"$existing"; then
    printf 'cron job %s exists; left as is\n' "$name"
    continue
  fi
  hermes -p dispatcher cron create "$schedule" "$(cat "$prompt_file")" --name "$name" --deliver "$deliver" --skill "$skill" \
    --paused --paused-reason "Awaiting repository allowlists, scoped credentials, and public webhook setup" >/dev/null
  printf 'created paused cron job %s (%s)\n' "$name" "$schedule"
done <<<"$cron_spec"

if [ "$gateway" = 1 ]; then
  hermes -p default gateway install >/dev/null
  hermes -p default gateway restart >/dev/null 2>&1 || hermes -p default gateway start >/dev/null
fi

printf 'team-kit Hermes side installed at kit %s.\n' "$ref"
printf 'Set kit_ref to %s in each repository'"'"'s .agents/dispatch.json.\n' "$ref"
printf 'Webhook secret: %s/webhook-secret. Next: the setup-dispatch skill, steps 7 to 12.\n' "$kit_home"
