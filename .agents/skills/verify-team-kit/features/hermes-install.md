# Install the Hermes module

The owner installs the team kit's Hermes module once per machine: `install.sh` clones the pinned kit, links the commands into `~/.hermes/bin/`, writes `guard.yaml`, and refreshes every bot's files.

## Sub-features

- `install-fresh`: a first install creates the kit checkout, the commands, and the guard file.
- `install-refresh`: a second install at the same commit changes nothing a bot depends on.
- `install-bootstrap`: `--bootstrap-bot NAME` creates the bootstrap bot's profile.

## How to get to it (user POV)

- Run `bash hermes/install.sh --ref <commit> [--bootstrap-bot NAME]` from a kit checkout.

## Driving it with sandbox.sh

- **Fresh install.** `sandbox.sh up` runs `install.sh --ref $(git rev-parse HEAD) --source <this checkout> --no-gateway`. Read `.verify-evidence/<run>/install.txt`: it ends with `team-kit Hermes module installed at kit <HEAD>`.
- **State.** `sandbox.sh evidence install-state bash -c 'ls "$HOME/.hermes/bin"; git -C "$HOME/.hermes/team-kit/kit" rev-parse HEAD; head -3 "$HOME/.hermes/team-kit/guard.yaml"'`. Proof: `team-kit-admin` and `team-kit-gh` are listed, the kit commit is `HEAD`.
- **Refresh.** `sandbox.sh evidence install-again bash -c 'bash "$TEAM_KIT_SOURCE_URL/hermes/install.sh" --ref "$(git -C "$TEAM_KIT_SOURCE_URL" rev-parse HEAD)" --source "$TEAM_KIT_SOURCE_URL" --no-gateway'`. Proof: exit 0, and `trusted_authors` in `guard.yaml` still lists `verify-owner`.
- **Bootstrap bot.** Add `--bootstrap-bot boot` to the refresh command. Proof: `$HOME/.hermes/profiles/boot/SOUL.md` exists and `team-kit-admin status` lists `boot` with role `bootstrap`.

## Gotchas

- `install.sh` installs the committed `--ref`, so an uncommitted edit is not what you verify.
- `--no-gateway` keeps the sandbox from touching a launchd or systemd service; never drop it in the sandbox.
