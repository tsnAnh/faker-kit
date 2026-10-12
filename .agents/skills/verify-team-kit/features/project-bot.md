# Set up a project and run its bot's commands

`team-kit-admin add-project` creates a project bot for one repository: its profile, paused jobs, labels on the repository, and the launch checkout Claude Code sessions start from. The bot then runs `team-kit-gh`, which reads the repository's dispatch files.

## Sub-features

- `add-project`: profile, five paused jobs, the team-kit labels, the launch checkout, and `bots.yaml`.
- `add-project-flags`: `--tick-every`, `--incidents-every`, `--security-every`, `--model`, `--claude-subscription`.
- `bot-project`: `team-kit-gh project` names the bot's project and repository.
- `bot-kit-check`: `team-kit-gh kit-check` compares the machine's kit with the repository's pin.

## How to get to it (user POV)

- The owner runs `team-kit-admin add-project --bot B --project P --repo OWNER/REPO` (or asks the bootstrap bot, or runs `/setup-hermes-project`).
- The project bot runs `team-kit-gh <command>` from its Hermes profile; its jobs run the same commands.

## Driving it with sandbox.sh

- **Add the project.** `sandbox.sh evidence add-project bash -c '"$HOME/.hermes/bin/team-kit-admin" add-project --bot shop-bot --project shop --repo acme/shop'`. Exit 0, JSON with `"bot": "shop-bot"` and `launch_checkout`.
- **Side effects.** `sandbox.sh evidence add-project-state bash -c 'python3 -c "import json,os; h=os.path.expanduser(\"~\"); print(len(json.load(open(h+\"/.fake-gh/labels.json\"))[\"acme/shop\"]), [(j[\"name\"], j[\"enabled\"]) for j in json.load(open(h+\"/.hermes/profiles/shop-bot/jobs.json\"))])"; test -d "$HOME/.hermes/team-kit/checkouts/acme__shop/repo/.git" && echo cloned'`. Proof: 12 labels, five jobs all `False` (paused), `cloned`.
- **Seed the repository's dispatch files** so the bot's commands have something to read: write `~/.fake-gh/files.json` with `.agents/dispatch.json` (`backend`, `kit_ref` = the sandbox kit commit, `trusted_dispatchers`) and `.agents/team-kit/KIT.json` (`version: 1`, the same `kit_ref`).
- **Bot commands.** `sandbox.sh evidence bot-commands bash -c 'export HERMES_HOME="$HOME/.hermes/profiles/shop-bot" GH_TOKEN=verify-token; "$HOME/.hermes/bin/team-kit-gh" project; "$HOME/.hermes/bin/team-kit-gh" kit-check'`. Proof: `{"bot": "shop-bot", "project": "shop", "repo": "acme/shop"}`, and `kit`, `kit_ref`, and `vendored` all equal.

## Gotchas

- Without `HERMES_HOME` set to the bot's profile, `team-kit-gh` refuses: it only runs as a project bot.
- `--claude-subscription` needs a real `claude` CLI that is logged in; in a session without one it refuses before changing anything, which is itself a provable path.
- The GitHub double answers only the calls the tests model. A command that needs another call fails loudly; extend `hermes/tests/fake_gh.py` the way the real `gh` answers, not to make the proof pass.
