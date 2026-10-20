# Install the Hermes module

The owner installs faker-kit's Hermes module once per machine: `install.sh` clones the pinned kit, writes the `faker-kit` wrapper into `~/.hermes/bin/`, writes `guard.yaml`, refreshes every bot's files, and, when a machine config is kept there, refreshes the bots, webhook routes, and CI runner services.

## Sub-features

- `install-state`: briefs, checkouts, andon records, and policy in `~/.hermes/faker-kit/` stay intact; one `faker-kit` wrapper is written; the guard plugin is installed in every bot's profile; `faker-kit-*` jobs are created.
- `install-refresh`: a second install at the same commit changes nothing.

## How to get to it (user POV)

- Run `bash hermes/install.sh --ref <commit> [--bootstrap-bot NAME]` from a kit checkout.

## Driving it

`python3 hermes/e2e/run_dispatch_e2e.py --artifact <path>`. Proof: the steps starting "install.sh succeeds", "install.sh keeps the briefs", "install.sh writes one faker-kit wrapper", "install.sh creates the project bot's faker-kit jobs", "running install.sh again changes nothing" all print `ok`.

## Gotchas

- `install.sh` installs the committed `--ref`; the e2e builds a throwaway git repository from the working tree, so uncommitted edits are what it checks.
- It runs with `--no-gateway`; never drop that outside a real machine.
