---
name: setup-cloud-agents
description: Make the repository start, build, test, and run its app in a fresh cloud container, so engineers can run agents in parallel and designers, marketers, and founders can use the kit from a browser or phone with nothing installed. Writes one idempotent setup script, makes the kit load in every cloud session, wires it into Claude Code on the web, Codex cloud, and Pi remote runs, picks how screenshots reach pull requests for each surface, and proves it all in a fresh session. Use for /setup-cloud-agents, "run agents in the cloud", "let non-engineers use this without installing anything", or before scaling past a handful of parallel agents.
---

# Set up cloud agents

A cloud session is a fresh machine per task. That does two jobs:

- **Scale.** Local worktrees cap parallel agents at roughly ten: each copy costs disk, CPU, and the one simulator or emulator a laptop can run. Cloud sessions each get their own machine.
- **Access.** A designer, marketer, or founder opens claude.ai/code, the Claude app on desktop or phone, or Codex on the web, picks the repository, and types `/start-here`. No terminal, no toolchain, nothing to break on their laptop.

The work is making the repository boot from nothing, reliably, with the kit loaded and a way to show pictures of the result.

## 1. Write one setup script

Create `scripts/cloud-setup.sh`. It must:

- Install exactly what `scripts/check.sh` and the verification skill need: language toolchains at the versions the repository pins, dependencies, build tools, and a headless browser for web apps.
- Be idempotent: running it twice leaves the same result.
- Seed local test data and test accounts from committed fixtures. Read secrets only from environment variables; never write them to disk or logs.
- Finish in a few minutes, and print what it installed.

Run it in a clean container (for example `docker run --rm -v "$PWD:/repo" -w /repo ubuntu:24.04 bash scripts/cloud-setup.sh`) until it succeeds from nothing.

## 2. Make the kit load in every session

A cloud container starts with no plugins installed by any person, so the repository has to bring the kit with it.

1. Commit `.claude/settings.json` from `templates/claude-settings.json` if `setup-team-kit` has not already. It declares the team-kit marketplace and enables its plugins for everyone who opens the repository.
2. Allow the network access the kit needs: GitHub, for the marketplace and plugin downloads.
3. Link the repository's own skills with `scripts/link-skills.sh` (from `setup-team-kit`), so they load even where plugins do not.

Step 5 proves that `/start-here` resolves in a fresh session. If it does not, add `claude plugin marketplace add tsnAnh/team-kit` and `claude plugin install trust-ladder@team-kit` to the end of `scripts/cloud-setup.sh`, guarded by `CLAUDE_CODE_REMOTE`.

## 3. Wire it into each agent's cloud

- **Claude Code on the web** (also what the Claude desktop and mobile apps run). In the cloud environment's settings, set the setup script to `bash scripts/cloud-setup.sh`, set network access to what the script and step 2 need, and add environment variables and API credentials. The result is cached between sessions. For steps that must run in every session, such as starting a local database, add a `SessionStart` hook to `.claude/settings.json` that runs only when `CLAUDE_CODE_REMOTE` is `true`. Name the environment after the repository, so non-engineers can pick it without asking.
- **Codex cloud.** In the Codex environment for this repository, set the setup script to `bash scripts/cloud-setup.sh` and add the same secrets and network settings. Codex reads the commands in AGENTS.md and the skills linked into `.agents/skills`. Check in step 5 whether it also loads the kit's plugins; if it does not, tell non-engineers to use Claude Code on the web.
- **Pi.** Pi has no managed cloud. Run Pi in a remote container or VM prepared by the same script, or use pi-subagents background runs in worktrees for small fan-outs. Do not point non-engineers at Pi.

Give non-engineers access: each needs a claude.ai (or ChatGPT, for Codex) seat on the team plan, the GitHub connection for the repository, and read access to it. Branch protection (`templates/branch-protection.md`) is what keeps their pull requests safe, so turn it on first.

## 4. Decide how each surface shows its proof

For every user-facing surface, pick how a cloud session proves a change with pictures, and how those pictures reach the pull request (`references/evidence-in-prs.md`):

| Surface | Proof in a cloud container | Where the rest is verified |
|---|---|---|
| Web app | Drive it headless with `control-ui`; screenshots and traces | Per-pull-request preview deployments, if the team has them, give the requester a link to click |
| Service, CLI, TUI | `control-cli` transcripts, responses, database rows | None needed |
| Android, KMP Compose | JVM screenshot tests (Roborazzi or Paparazzi) and Robolectric; no emulator, since most containers lack KVM | A KVM-enabled or macOS CI runner, or a device farm, for on-device checks |
| Flutter | `flutter test` golden images and the web target | As Android and iOS, for platform-specific behavior |
| iOS | None: the Simulator needs macOS | A GitHub Actions macOS runner posts screenshots per pull request (`templates/github/ios-snapshots.yml`). Xcode Cloud to TestFlight, or fastlane on the same runner, puts a build on the requester's phone |

For mobile apps, add screenshot tests for the main screens if the repository has none: they are the only pictures a Linux cloud session can take, and they catch visual regressions in `check` from then on.

Write the result into AGENTS.md under a `Cloud sessions` heading: for each surface, the command that proves it in the cloud, and where the rest is verified. `start-here` and `request-a-change` read that section to tell a requester what proof to expect.

## 5. Prove it

Start a fresh cloud session in each agent the team uses. In it:

1. Run `scripts/check.sh`, which must pass.
2. Run the verification skill's `doctor` command, which must report healthy for every surface the cloud can run.
3. Type `/start-here` (Codex: `$start-here`). It must ask what you want to do.
4. Run a small `/request-a-change`, such as a copy change, through to a pull request. Its body must show the before and after images, and anyone on the team must be able to see them.

Record the time from session start to a passing check. If it is over ten minutes, cache more in the setup script.

## Report

The setup script, where it is wired in each agent, whether the kit loads in a fresh session, the proof for each surface and where the rest is verified, the link to the test pull request, the time to a passing check, and the next steps: send non-engineers [the browser guide](https://github.com/tsnAnh/team-kit/blob/main/docs/how-to/use-the-kit-from-the-browser.md), and run `trust-audit` to see how many agents the repository can take in parallel.
