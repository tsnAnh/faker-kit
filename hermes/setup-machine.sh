#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: hermes/setup-machine.sh [--config FILE] [--ref COMMIT] [--yes]

Builds a whole faker-kit Hermes machine from one machine config, so the same setup can be rebuilt
anywhere. Run it from a clone of the kit repository, on Linux (Debian, Ubuntu, or Raspberry Pi OS;
amd64 or arm64, with systemd) or macOS. Run it again at any time: every step checks first and
changes only what differs.

  --config FILE  The machine config (default: .agents/hermes-machine.yaml in this clone).
  --ref COMMIT   Kit commit to install (default: the config's kit_ref).
  --yes          Do not stop to ask before installing packages.

It installs what is missing (gh, Claude Code, Hermes, cloudflared, and Python with PyYAML), asks you
to sign in where a login is missing, asks for tokens with hidden input and writes them only to the
profiles' .env files, installs the kit and its bots (faker-kit install), publishes the signed
webhook paths through a Cloudflare Tunnel, runs every service in the background, and checks the
result. Nothing secret is ever passed on a command line, printed, or written to the config.
USAGE
}

say() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf 'setup-machine: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/.." && pwd)
config=$repo/.agents/hermes-machine.yaml
ref=''
assume_yes=0
while [ $# -gt 0 ]; do
  case $1 in
    --config) config=$2; shift 2 ;;
    --ref) ref=$2; shift 2 ;;
    --yes) assume_yes=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument $1. Run --help for usage." ;;
  esac
done
[ -f "$config" ] || die "no machine config at $config; copy hermes/templates/machine.yaml there and fill it in"
config=$(cd "$(dirname "$config")" && pwd)/$(basename "$config")

os=$(uname -s)
case $os in
  Linux) have systemctl || die "Linux needs systemd to keep Hermes running" ;;
  Darwin) ;;
  *) die "unsupported OS $os; use Linux or macOS" ;;
esac
user=${USER:-$(id -un)}
export PATH="$HOME/.local/bin:$HOME/.hermes/bin:$PATH"
[ "$os" = Darwin ] && export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

confirm() {
  [ "$assume_yes" = 1 ] && return 0
  local answer
  read -r -p "    $1 [Y/n] " answer || return 1   # no terminal and no --yes: never assume yes
  case $answer in ''|y|Y|yes) return 0 ;; *) return 1 ;; esac
}

# KEY=VALUE into a .env file, mode 600, other lines kept. Bash builtins only, so the value never
# appears in a process's arguments.
env_set() {
  local file=$1 key=$2 value=$3 tmp line
  mkdir -p "$(dirname "$file")"
  tmp=$(mktemp "$(dirname "$file")/.env.XXXXXX")
  chmod 600 "$tmp"
  if [ -f "$file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      [[ $line == "$key="* ]] || printf '%s\n' "$line"
    done < "$file" > "$tmp"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$tmp"
  mv "$tmp" "$file"
}

env_has() { [ -f "$1" ] && grep -q "^$2=." "$1"; }

ask_secret() {
  local prompt=$1 value
  read -r -s -p "    $prompt: " value
  printf '\n' >&2
  printf '%s' "$value"
}

# ---------------------------------------------------------------------------------------------------
say "1/8 System packages"
apt_install() {  # packages, with the GitHub CLI and Cloudflare apt repositories added when asked for
  have apt-get || die "this Linux has no apt-get; install $* yourself, then run this again"
  local keyrings=/etc/apt/keyrings
  sudo install -d -m 0755 "$keyrings"
  if [[ " $* " == *" gh "* ]]; then
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee "$keyrings/githubcli-archive-keyring.gpg" >/dev/null
    echo "deb [arch=$(dpkg --print-architecture) signed-by=$keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  fi
  if [[ " $* " == *" cloudflared "* ]]; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee "$keyrings/cloudflare-main.gpg" >/dev/null
    echo "deb [signed-by=$keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
      | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  fi
  sudo apt-get update -q
  sudo apt-get install -y -q ca-certificates "$@"
}
install_packages() {
  [ $# -gt 0 ] || return 0
  note "missing: $*"
  confirm "Install them now?" || die "install $*, then run this again"
  if [ "$os" = Linux ]; then
    apt_install "$@"
  else
    have brew || die "install Homebrew (https://brew.sh), then run this again"
    local pkg
    for pkg in "$@"; do
      case $pkg in
        *) brew install "$pkg" ;;
      esac
    done
  fi
}

# Python first: it reads the config, which is checked before anything else changes. PyYAML comes from hermes/vendor.
first=()
have curl || first+=(curl)
have python3 || first+=(python3)
install_packages "${first[@]+"${first[@]}"}"
PYTHONPATH=$here/lib:$here/vendor python3 -c 'import sys, pathlib, faker_kit_machine as m; m.load_machine(pathlib.Path(sys.argv[1]))' "$config" \
  || die "fix $config first"
read_config() { PYTHONPATH=$here/lib:$here/vendor python3 -c "import sys, pathlib, faker_kit_machine as m; c = m.load_machine(pathlib.Path(sys.argv[1])); $1" "$config"; }
bootstrap_bot=$(read_config 'print(c.bootstrap_bot)')
subscription=$(read_config 'print(1 if c.claude_subscription else 0)')
project_bots=$(read_config 'print(" ".join(p.bot for p in c.projects))')
webhook_url=$(read_config 'print(c.webhook_url or "")')
tunnel=$(read_config 'print(c.tunnel or "")')
slack_bot=$(read_config 'print(c.slack.bot if c.slack else "")')
ci_runners=$(read_config 'print(" ".join(r.repo for r in c.ci_runners))')
[ -n "$ref" ] || ref=$(read_config 'print(c.kit_ref or "")')
[[ $ref =~ ^[0-9a-f]{40}$ ]] || die "no kit commit: pass --ref, or set kit_ref in $config"
git -C "$repo" cat-file -e "$ref:hermes/lib/faker_kit_machine.py" 2>/dev/null \
  || die "kit $ref has no machine setup (or this clone lacks it: git fetch); pass a newer --ref, or move kit_ref forward"

rest=()
have git || rest+=(git)
have gh || rest+=(gh)
[ -n "$webhook_url" ] && ! have cloudflared && rest+=(cloudflared)
install_packages "${rest[@]+"${rest[@]}"}"
if [ -n "$ci_runners" ]; then
  have docker && docker info >/dev/null 2>&1 \
    || die "ci_runners runs CI jobs in Docker containers: install Docker (Docker Desktop on macOS), start it, and run this again"
fi

# ---------------------------------------------------------------------------------------------------
say "2/8 Claude Code and Hermes"
if ! have claude; then
  confirm "Install Claude Code with its official installer?" || die "install Claude Code, then run this again"
  curl -fsSL https://claude.ai/install.sh | bash
fi
if ! have hermes; then
  confirm "Install Hermes with its official installer?" || die "install Hermes, then run this again"
  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
fi
have claude || die "claude is not on PATH after installing; open a new shell and run this again"
have hermes || die "hermes is not on PATH after installing; open a new shell and run this again"
note "claude $(claude --version 2>/dev/null | head -1); hermes $(hermes --version 2>/dev/null | head -1)"

# ---------------------------------------------------------------------------------------------------
say "3/8 Sign-ins"
if ! gh auth status >/dev/null 2>&1; then
  note "GitHub: sign in as the repositories' owner (a browser code works on a machine without a screen)"
  gh auth login --git-protocol https --scopes admin:repo_hook
elif [ -n "$webhook_url" ] && ! gh auth status 2>&1 | grep -q "admin:repo_hook"; then
  note "GitHub: the webhooks need the admin:repo_hook scope"
  gh auth refresh --scopes admin:repo_hook
fi
gh auth setup-git >/dev/null
if [ "$subscription" = 1 ] && ! claude auth status >/dev/null 2>&1; then
  note "Claude: sign in to the subscription the bots run on"
  claude auth login
fi
if [ -n "$webhook_url" ] && [ ! -f "$HOME/.cloudflared/cert.pem" ]; then
  note "Cloudflare: pick the zone of $(read_config 'print(c.webhook_url.split("//")[1])') in the page this opens"
  cloudflared tunnel login
fi

# ---------------------------------------------------------------------------------------------------
say "4/8 Kit $ref"
# The clone's own remote, so a private or renamed kit repository installs from where it was cloned.
source_url=$(git -C "$repo" remote get-url origin 2>/dev/null || true)
[ -n "$source_url" ] || source_url=$repo
# The gateway starts once, in step 7, after the webhook routes exist.
install_args=(--ref "$ref" --source "$source_url" --bootstrap-bot "$bootstrap_bot" --no-gateway)
[ "$subscription" = 1 ] && install_args+=(--claude-subscription)
mkdir -p "$HOME/.hermes/faker-kit"
install -m 600 "$config" "$HOME/.hermes/faker-kit/machine.yaml"
bash "$here/install.sh" "${install_args[@]}"
kitlib=$HOME/.hermes/faker-kit/kit/hermes/lib

# ---------------------------------------------------------------------------------------------------
say "5/8 Bots and webhooks"
# A plain shell, never a bot profile: faker-kit install runs only here.
result=$(env -u HERMES_HOME faker-kit install --config "$config")
python3 - "$result" <<'PY'
import json, sys
out = json.loads(sys.argv[1])
for project in out["projects"]:
    print(f"    {project['bot']}: {project['repo']}")
if out["skills"]:
    print(f"    kit skills in: {', '.join(out['skills'])}")
for hook in out["webhooks"]:
    print(f"    webhook {hook['hook']}: {hook['url']}")
for project in out["projects"]:
    if not project["jobs_active"]:
        print(f"    then: sign gh in for {project['bot']}, or put its GH_TOKEN in its .env, so its jobs start")
PY
checkouts=$(python3 -c 'import json, sys; print("\n".join(p["launch_checkout"] for p in json.loads(sys.argv[1])["projects"]))' "$result")

# ---------------------------------------------------------------------------------------------------
say "6/8 Tokens"
profiles=$HOME/.hermes/profiles
token_stored=0
for bot in $project_bots; do
  env_file=$profiles/$bot/.env
  if env_has "$env_file" GH_TOKEN; then
    note "$bot: GH_TOKEN is set"
  else
    # Every bot shares this machine's gh login unless it has a token of its own (another account).
    note "$bot uses this machine's gh login. Press Enter to keep that, or paste a token for a separate bot account."
    token=$(ask_secret "GH_TOKEN for $bot")
    if [ -n "$token" ]; then
      env_set "$env_file" GH_TOKEN "$token"
      token_stored=1
    fi
    unset token
  fi
done
[ "$token_stored" = 0 ] || env -u HERMES_HOME faker-kit install --config "$config" >/dev/null
if [ -n "$slack_bot" ]; then
  env_file=$HOME/.hermes/.env
  for key in SLACK_APP_TOKEN SLACK_BOT_TOKEN; do
    env_has "$env_file" "$key" && continue
    value=$(ask_secret "$key for the Slack app (xapp-… for the app token, xoxb-… for the bot token)")
    [ -n "$value" ] || die "$key is required while the config enables Slack"
    env_set "$env_file" "$key" "$value"
    unset value
  done
  env_has "$env_file" SLACK_ALLOWED_USERS || {
    value=$(ask_secret "SLACK_ALLOWED_USERS: the Slack member ids (U…) that may talk to the bots, comma-separated")
    [ -n "$value" ] || die "SLACK_ALLOWED_USERS is required while the config enables Slack, so no one else in the workspace reaches a bot"
    env_set "$env_file" SLACK_ALLOWED_USERS "$value"
    unset value
  }
fi

# ---------------------------------------------------------------------------------------------------
say "7/8 Background services"
launchd_load() {  # label, plist: (re)load a launchd agent, waiting for the old one to be gone first
  local label=$1 plist=$2 domain
  domain=gui/$(id -u)
  launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do launchctl print "$domain/$label" >/dev/null 2>&1 || break; sleep 0.5; done
  launchctl bootstrap "$domain" "$plist"
}
service_user() {  # name, then the command and its arguments: a user service that restarts on failure
  local name=$1
  shift
  if [ "$os" = Linux ]; then
    local unit=$HOME/.config/systemd/user/$name.service
    mkdir -p "$(dirname "$unit")"
    python3 - "$name" "$@" > "$unit.new" <<'PY'
import sys
name, argv = sys.argv[1], sys.argv[2:]
quoted = " ".join('"' + a.replace("\\", "\\\\").replace('"', '\\"') + '"' for a in argv)
print(f"[Unit]\nDescription={name}\nAfter=network-online.target\nWants=network-online.target\n\n"
      f"[Service]\nType=simple\nExecStart={quoted}\nRestart=always\nRestartSec=10\n\n[Install]\nWantedBy=default.target")
PY
    mv "$unit.new" "$unit"
    systemctl --user daemon-reload
    systemctl --user enable "$name.service" >/dev/null 2>&1
    systemctl --user restart "$name.service"
  else
    local plist=$HOME/Library/LaunchAgents/$name.plist
    mkdir -p "$(dirname "$plist")"
    python3 -c 'import plistlib, sys; sys.stdout.buffer.write(plistlib.dumps({"Label": sys.argv[1], "ProgramArguments": sys.argv[2:], "RunAtLoad": True, "KeepAlive": True}))' \
      "$name" "$@" > "$plist"
    launchd_load "$name" "$plist"
  fi
}
if [ "$os" = Linux ]; then
  # Without lingering, systemd stops every user service at logout, Hermes included.
  loginctl show-user "$user" -p Linger 2>/dev/null | grep -q yes || sudo loginctl enable-linger "$user"
  # Hermes Desktop reaches this machine over SSH; Raspberry Pi OS ships with it off.
  if ! systemctl is-active --quiet ssh 2>/dev/null && ! systemctl is-active --quiet sshd 2>/dev/null; then
    have sshd || sudo apt-get install -y -q openssh-server
    sudo systemctl enable --now ssh 2>/dev/null || sudo systemctl enable --now sshd
  fi
fi
hermes -p default gateway install >/dev/null
hermes -p default gateway restart >/dev/null 2>&1 || hermes -p default gateway start >/dev/null
note "Hermes gateway: running"

if [ -n "$webhook_url" ]; then
  public_host=${webhook_url#https://}
  tunnel_id() {
    local listed
    listed=$(cloudflared tunnel list --output json) || die "cloudflared tunnel list failed"
    python3 -c 'import json, sys; print(next((t["id"] for t in (json.loads(sys.argv[1]) or []) if t.get("name") == sys.argv[2]), ""))' "$listed" "$tunnel"
  }
  id=$(tunnel_id)
  if [ -z "$id" ]; then
    cloudflared tunnel create "$tunnel" >/dev/null
    id=$(tunnel_id)
    [ -n "$id" ] || die "cloudflared did not create tunnel $tunnel"
  fi
  creds=$HOME/.cloudflared/$id.json
  # On a rebuilt machine the tunnel already exists, but its credentials file stayed on the old one.
  # --cred-file writes it to disk instead of printing the token.
  [ -f "$creds" ] || cloudflared tunnel token --cred-file "$creds" "$tunnel" >/dev/null
  chmod 600 "$creds"
  cloudflared tunnel route dns --overwrite-dns "$tunnel" "$public_host" >/dev/null
  tunnel_config=$HOME/.cloudflared/$tunnel.yml
  PYTHONPATH=$kitlib:$kitlib/../vendor python3 -c 'import sys, pathlib, faker_kit_machine as m; sys.stdout.write(m.cloudflared_config(sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3]))' \
    "$id" "$creds" "$public_host" > "$tunnel_config"
  service_user faker-kit-tunnel "$(command -v cloudflared)" --no-autoupdate --config "$tunnel_config" tunnel run
  note "Cloudflare Tunnel $tunnel: https://$public_host serves only this machine's signed webhook routes"
fi
# Also stops the runner services of repositories the config no longer lists.
faker-kit runner up --config "$config" >/dev/null || die "the CI runners did not start; see faker-kit runner's message above"
[ -z "$ci_runners" ] || note "CI runners: $ci_runners, one fresh container per job; workflows use runs-on: [self-hosted, faker-kit]"

# ---------------------------------------------------------------------------------------------------
say "8/8 Checks"
for bot in $project_bots; do
  HERMES_HOME=$profiles/$bot faker-kit status >/dev/null || die "$bot: status failed; run: HERMES_HOME=$profiles/$bot faker-kit status"
  note "$bot: GitHub and the repository's dispatch settings answer"
done
if [ -n "$webhook_url" ]; then
  for _ in $(seq 1 30); do curl -fsS --max-time 2 http://127.0.0.1:8644/health >/dev/null 2>&1 && break; sleep 2; done
  curl -fsS --max-time 2 http://127.0.0.1:8644/health >/dev/null || die "the Hermes webhook listener is not answering on 127.0.0.1:8644"
  note "webhook listener: up"
  # A signed ping goes through the public address: tunnel, route, and secret. Hermes answers 200 for an event the route does
  # not list, after checking the signature, so no model run starts. A new tunnel or DNS record takes a little while,
  # so it retries for about a minute.
  for bot in $project_bots; do
    PYTHONPATH=$kitlib:$kitlib/../vendor python3 - "$webhook_url" "$bot" "$HOME/.hermes/.env" <<'PY' || die "$bot: no signed test event reached Hermes through $webhook_url"
import hashlib, hmac, json, sys, time, urllib.error, urllib.request, uuid
import faker_kit_machine as m
url, bot, env = sys.argv[1], sys.argv[2], sys.argv[3]
key = m.secret_env(bot)
secret = next(line.split("=", 1)[1] for line in open(env).read().splitlines() if line.startswith(key + "="))
body = json.dumps({"zen": "faker-kit setup check"}).encode()
signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
for attempt in range(12):
    request = urllib.request.Request(m.route_url(url, bot, f"{bot}-pr"), data=body, method="POST", headers={
        "Content-Type": "application/json", "X-GitHub-Event": "ping", "X-GitHub-Delivery": str(uuid.uuid4()),
        "X-Hub-Signature-256": signature, "User-Agent": "faker-kit-setup"})
    try:
        if urllib.request.urlopen(request, timeout=20).status == 200:
            sys.exit(0)
    except (urllib.error.URLError, OSError):
        pass
    time.sleep(5)
sys.exit(1)
PY
    note "$bot: a signed event through $webhook_url accepted a signed ping"
  done
fi

# Claude Code asks once per folder whether to trust it; a launch cannot answer, so you do it here.
claude_json=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.claude.json
[ -f "$claude_json" ] || claude_json=$HOME/.claude.json
while IFS= read -r checkout <&3; do
  [ -n "$checkout" ] || continue
  trusted=$(python3 -c 'import json, sys
try:
    d = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    d = {}
print(1 if ((d.get("projects") or {}).get(sys.argv[2]) or {}).get("hasTrustDialogAccepted") else 0)' "$claude_json" "$checkout")
  if [ "$trusted" = 1 ]; then
    note "launch folder trusted: $checkout"
  elif [ -t 0 ] && [ -t 1 ]; then
    note "Claude Code opens in $checkout now: choose Yes to trust it, then type /exit"
    (cd "$checkout" && claude) || true
  else
    note "still to do: cd $checkout && claude, choose Yes, then /exit"
  fi
done 3<<< "$checkouts"

say "Done"
note "Chat: in Hermes Desktop, Gateways > Add connection > SSH, host $user@$(hostname)."
if [ -n "$slack_bot" ]; then note "Slack: Hermes's own Slack platform serves $slack_bot. SLACK_ALLOWED_USERS in $HOME/.hermes/.env limits who may talk to it."; fi
note "Run this script again after any change to $config."
