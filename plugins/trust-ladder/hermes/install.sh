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
  --no-gateway            Do not install or restart the dispatcher's background gateway

It writes, under ~/.hermes:
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

bash "$here/../skills/setup-cloud-agents/scripts/install-kit-skills.sh" \
  --ref "$ref" --source "$source" --checkout "$kit" --dest "$kit_home/all-skills" >/dev/null
[ "$(git -C "$kit" rev-parse HEAD)" = "$ref" ] || die "kit checkout is not at $ref"
pkg=$kit/plugins/trust-ladder/hermes
skills=$kit/plugins/trust-ladder/skills

shebang=$(head -n 1 "$(command -v hermes)")
case $shebang in
  '#!'*python*) py=${HERMES_PYTHON:-${shebang#\#!}} ;;
  *) py=${HERMES_PYTHON:-python3} ;;
esac
$py -c 'import yaml' 2>/dev/null || die "$py cannot import yaml; set HERMES_PYTHON to Hermes's python"

merge_yaml() {
  $py - "$@" <<'PY'
import sys, yaml
from pathlib import Path

target, snippet = Path(sys.argv[1]), Path(sys.argv[2])
secret = sys.argv[3] if len(sys.argv) > 3 else None
text = snippet.read_text()
if secret is not None:
    text = text.replace("@TEAM_KIT_WEBHOOK_SECRET@", secret)
ours = yaml.safe_load(text) or {}
theirs = yaml.safe_load(target.read_text()) if target.exists() else {}
theirs = theirs or {}

def merge(base, add, path=()):
    for key, value in add.items():
        here = path + (key,)
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value, here)
        elif here == ("plugins", "enabled") and isinstance(base.get(key), list):
            base[key] = base[key] + [v for v in value if v not in base[key]]
        else:
            base[key] = value

merge(theirs, ours)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(yaml.safe_dump(theirs, sort_keys=False))
PY
}

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
  (umask 077; $py -c 'import secrets; print(secrets.token_hex(32))' > "$kit_home/webhook-secret")
fi
secret=$(cat "$kit_home/webhook-secret")

if [ ! -f "$kit_home/guard.yaml" ]; then
  install -m 600 "$kit/plugins/trust-ladder/templates/dispatch/guard.yaml" "$kit_home/guard.yaml"
  printf 'wrote %s; fill in repos and trusted_authors\n' "$kit_home/guard.yaml"
fi

for profile in dispatcher launcher; do
  home=$root/profiles/$profile
  [ -d "$home" ] || hermes profile create "$profile" >/dev/null
  [ -d "$home" ] || die "hermes profile create $profile did not create $home"
  merge_yaml "$home/config.yaml" "$pkg/profiles/$profile/config.yaml"
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
merge_yaml "$dispatcher/config.yaml" "$pkg/webhook/config.yaml" "$secret"
for dest in "$root/skills" "$dispatcher/skills"; do
  link_skill dispatch "$dest"
  link_skill setup-dispatch "$dest"
done

scripts=$dispatcher/scripts/team-kit
mkdir -p "$scripts" "$root/bin"
cp "$pkg/webhook/github-triage.py" "$pkg/webhook/team_kit_contracts.py" "$pkg/profiles/dispatcher/team_kit_gh.py" "$scripts/"
cat > "$root/bin/team-kit-gh" <<SH
#!/usr/bin/env bash
exec $py "$scripts/team_kit_gh.py" "\$@"
SH
chmod 755 "$root/bin/team-kit-gh"

existing=$(hermes -p dispatcher cron list --all 2>/dev/null || true)
while IFS=$'\t' read -r name schedule deliver skill prompt_file; do
  if grep -qw -- "$name" <<<"$existing"; then
    printf 'cron job %s exists; left as is\n' "$name"
    continue
  fi
  hermes -p dispatcher cron create "$schedule" "$(cat "$prompt_file")" --name "$name" --deliver "$deliver" --skill "$skill" >/dev/null
  printf 'created cron job %s (%s)\n' "$name" "$schedule"
done < <($py - "$pkg/cron/dispatcher-drain.md" "$kit_home" <<'PY'
import re, sys
from pathlib import Path

text, out = Path(sys.argv[1]).read_text(), Path(sys.argv[2])
for block in re.split(r"^## ", text, flags=re.M)[1:]:
    name = block.split("\n", 1)[0].strip()
    field = lambda key: re.search(rf"^{key}: `([^`]+)`", block, re.M).group(1)
    prompt = re.search(r"```text\n(.*?)```", block, re.S).group(1).strip()
    prompt_file = out / f".cron-{name}.txt"
    prompt_file.write_text(prompt)
    print("\t".join([name, field("Schedule"), field("Deliver"), field("Skill"), str(prompt_file)]))
PY
)

if [ "$gateway" = 1 ]; then
  hermes -p dispatcher gateway install >/dev/null
  hermes -p dispatcher gateway restart >/dev/null 2>&1 || hermes -p dispatcher gateway start >/dev/null
fi

printf 'team-kit Hermes side installed at kit %s.\n' "$ref"
printf 'Set kit_ref to %s in each repository'"'"'s .agents/dispatch.json.\n' "$ref"
printf 'Webhook secret: %s/webhook-secret. Next: the setup-dispatch skill, steps 7 to 12.\n' "$kit_home"
