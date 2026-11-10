#!/usr/bin/env bash

faker_kit_environment_setup() {
  local config_dir=${config_dir:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}
  local installer=$config_dir/faker-kit-install.sh

  mkdir -p "$config_dir" || return 1
  {
    printf '#!/usr/bin/env bash\nconfig_dir=%q\n' "$config_dir"
    cat <<'FAKER_KIT_INSTALLER'
set -euo pipefail
exec 3>&1 4>&2
mkdir -p "$config_dir"
exec >>"$config_dir/faker-kit-setup.log" 2>&1
trap 'echo "faker-kit: the kit did not install; see $config_dir/faker-kit-setup.log" >&4' ERR
export GIT_TERMINAL_PROMPT=0
kit_ref=${FAKER_KIT_REF:-main}
kit_url=${FAKER_KIT_URL:-https://github.com/tsnAnh/faker-kit.git}
kit_dir=$HOME/.cache/faker-kit
compat=$kit_dir/plugins/trust-ladder/references/compat.md
if [ -f "$kit_dir/.faker-kit-installed" ]; then
  cat "$compat" >&3
  exit 0
fi
if [ -z "${FAKER_KIT_READ_TOKEN:-}" ]; then
  echo "faker-kit: FAKER_KIT_READ_TOKEN is not set; the kit is not installed"
  exit 0
fi
rm -rf "$kit_dir"
git init -q "$kit_dir"
if [[ $kit_url == https://github.com/* ]]; then
  header="Authorization: Basic $(printf 'x-access-token:%s' "$FAKER_KIT_READ_TOKEN" | base64 | tr -d '\n')"
  GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.extraHeader GIT_CONFIG_VALUE_0=$header \
    git -C "$kit_dir" fetch -q --depth 1 "$kit_url" "$kit_ref"
else
  git -C "$kit_dir" fetch -q --depth 1 "$kit_url" "$kit_ref"
fi
git -C "$kit_dir" checkout -q FETCH_HEAD
python3 "$kit_dir/plugins/trust-ladder/skills/setup-cloud-agents/scripts/kit_links.py" --kit "$kit_dir" --config "$config_dir"
touch "$kit_dir/.faker-kit-installed"
echo "faker-kit $(git -C "$kit_dir" rev-parse --short=12 HEAD) installed"
cat "$compat" >&3
FAKER_KIT_INSTALLER
  } > "$installer" || return 1
  chmod 0755 "$installer" || return 1

  python3 - "$config_dir/settings.json" "$installer" <<'FAKER_KIT_HOOK' || return 1
import json
import os
import shlex
import sys
from pathlib import Path

path, installer = Path(sys.argv[1]), sys.argv[2]
settings = json.loads(path.read_text().strip() or "{}") if path.exists() else {}
groups = settings.setdefault("hooks", {}).setdefault("SessionStart", [])
command = f"bash {shlex.quote(installer)}"
if not any(hook.get("command") == command for group in groups for hook in group.get("hooks", [])):
    groups.append({"hooks": [{"type": "command", "command": command, "timeout": 120}]})
    pending = path.with_name(path.name + ".pending")
    pending.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(pending, path)
FAKER_KIT_HOOK

  bash "$installer" >/dev/null || true
  printf 'faker-kit installs at session start through %s\n' "$installer"
}

faker_kit_environment_setup
