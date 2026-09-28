---
name: setup-cloud-agents
description: Make the repository start, build, test, and run its app in a fresh cloud container, so agents can work in parallel in the cloud instead of in local worktrees. Writes one idempotent setup script, wires it into Claude Code on the web, Codex cloud, and Pi remote runs, and proves it by running the checks and the verification skill's doctor in a fresh session. Use for /setup-cloud-agents, "run agents in the cloud", or before scaling past a handful of parallel agents.
---

# Set up cloud agents

Local worktrees cap parallel agents at roughly ten: each copy costs disk, CPU, and the one simulator or emulator your machine can run. Cloud sessions each get their own machine. They can install dependencies, run the app, and capture evidence while your laptop stays free. The work is making the repository boot from nothing, reliably.

## 1. Write one setup script

Create `scripts/cloud-setup.sh`. It must:

- Install exactly what `scripts/check.sh` and the verification skill need: language toolchains at the versions the repository pins, dependencies, and build tools.
- Be idempotent: running it twice leaves the same result.
- Seed local test data and test accounts from committed fixtures. Read secrets only from environment variables; never write them to disk or logs.
- Finish in a few minutes, and print what it installed.

Run it in a clean container (for example `docker run --rm -v "$PWD:/repo" -w /repo ubuntu:24.04 bash scripts/cloud-setup.sh`) until it succeeds from nothing.

## 2. Wire it into each agent's cloud

- **Claude Code on the web.** In the cloud environment's settings, set the setup script to `bash scripts/cloud-setup.sh`, set network access to what the script needs, and add environment variables and API credentials. The result is cached between sessions. For steps that must run in every session, such as starting a local database, add a `SessionStart` hook to `.claude/settings.json` that runs only when `CLAUDE_CODE_REMOTE` is `true`.
- **Codex cloud.** In the Codex environment for this repository, set the setup script to `bash scripts/cloud-setup.sh` and add the same secrets and network settings. Codex reads the commands in AGENTS.md.
- **Pi.** Pi has no managed cloud. Run Pi in a remote container or VM prepared by the same script, or use pi-subagents background runs in worktrees for small fan-outs.

## 3. Know what the cloud cannot run

Linux cloud containers can run web apps, services, CLIs, Flutter's tester and web targets, and JVM tests. They usually cannot run:

- **The iOS Simulator.** It needs macOS. Verify iOS changes on a macOS CI runner, like the swift-ios lint pack's CI job, or on a Mac that agents reach remotely.
- **A hardware-accelerated Android emulator.** Most containers lack KVM. Use a macOS or KVM-enabled runner, or a device farm, for on-device verification. Unit, Robolectric, and Compose tests run fine.

Write these limits into AGENTS.md under a `Cloud sessions` heading, with the command that verifies each surface in the cloud and where the rest gets verified.

## 4. Prove it

Start a fresh cloud session in each agent the team uses. In it, run:

1. `scripts/check.sh`, which must pass.
2. The verification skill's `doctor` command, which must report healthy for every surface the cloud can run.

Record the time from session start to a passing check. If it is over ten minutes, cache more in the setup script.

## Report

The setup script, where it is wired in each agent, the surfaces verifiable in the cloud and the ones that are not, the time to a passing check, and the next step: run `trust-audit` to see how many agents the repository can take in parallel.
