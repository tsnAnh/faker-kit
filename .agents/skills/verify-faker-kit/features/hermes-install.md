# Install the Hermes module

The owner installs faker-kit's Hermes module once per machine: `install.sh` clones the pinned kit, writes the `faker-kit` wrapper into `~/.hermes/bin/`, writes `guard.yaml`, refreshes every bot's files, and, when a machine config is kept there, refreshes the bots, webhook routes, and CI runner services.

## Sub-features

- `install-state`: briefs, checkouts, andon records, and policy in `~/.hermes/faker-kit/` stay intact; one `faker-kit` wrapper is written; the guard plugin is installed in every bot's profile; `faker-kit-*` jobs are created, and the chief gets its weekday `faker-kit-loop` and `faker-kit-digest` jobs that drive its `faker-kit handoff` and `faker-kit loop` commands. The chief's hourly loop messages no bot; each project bot's babysit wake tells the chief its units' news instead.
- `install-refresh`: a second install at the same commit changes nothing.
- `install-model-provider`: `install.sh --provider --model`, `faker-kit install --config` with `provider` and `model` fields, and `faker-kit add-project --provider --model` write the pair to the bot's profile and drop a stale subscription provider, `base_url`, and `api_mode`; a model alone, no flags, and `--claude-subscription` behave as before; a provider with no model, a provider with the subscription, and the subscription's own id as a provider are refused before anything is written; guard.yaml, the guard plugin, and every `.env` stay untouched.

## How to get to it (user POV)

- Run `bash hermes/install.sh --ref <commit> [--bootstrap-bot NAME]` from a kit checkout.

## Driving it

`python3 hermes/e2e/run_dispatch_e2e.py --artifact <path>`. Proof: the steps starting "install.sh succeeds", "install.sh keeps the briefs", "install.sh writes one faker-kit wrapper", "install.sh creates the project bot's faker-kit jobs", "install.sh makes the chief an engineering lead", "install.sh keeps bot chatter out of the owner's chat", "handoff records a part", "the digest names a merged part once", "guard: the chief's faker-kit handoff runs only in a clean turn", "running install.sh again changes nothing" all print `ok`. For `install-model-provider`: the steps starting "install.sh refuses a provider without its model", "install.sh --provider --model writes the pair", "faker-kit add-project --provider", "faker-kit install --config with", "install --config writes each chosen provider", "a project with only a model", "add-project --claude-subscription still sets", "a new project bot with no flags inherits", "install with the same config again", and "the provider path writes no credential" all print `ok`.

## Gotchas

- `install.sh` installs the committed `--ref`; the e2e builds a throwaway git repository from the working tree, so uncommitted edits are what it checks.
- It runs with `--no-gateway`; never drop that outside a real machine.
