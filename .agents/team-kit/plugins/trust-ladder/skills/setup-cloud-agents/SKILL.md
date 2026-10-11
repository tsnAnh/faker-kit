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

A cloud session starts from a clone of the repository and nothing else. It does not install the plugins `.claude/settings.json` enables, Codex cloud loads no plugins at all, and neither can clone the kit when the kit's repository is private: the session's GitHub access reaches only the repository it was started on. Both do load skills committed to the repository: Claude Code from `.claude/skills/` and `.claude/agents/`, Codex and Pi from `.agents/skills/`. So the repository carries the kit with it.

1. Vendor the kit. From a checkout of the kit at the commit the team pins (the one in `.pi/settings.json` and, for dispatch, `.agents/dispatch.json`), run:

   ```bash
   python3 plugins/trust-ladder/skills/setup-cloud-agents/scripts/vendor_kit.py --kit . --into <repository>
   ```

   It copies every kit plugin into `.agents/team-kit/` (without their docs and the kit's own tests), records the commit and a hash of every file in `.agents/team-kit/KIT.json`, and links each kit skill into `.claude/skills/` and `.agents/skills/` and each subagent into `.claude/agents/`. When the repository already has its own skill of a kit skill's name, such as its own `follow-brief`, the kit's is linked nowhere, so every agent runs the repository's; the script lists what it kept. It also moves `kit_ref` in `.agents/dispatch.json` to the vendored commit, and refuses when the repository's ignore rules would leave a vendored file or link out of the commit. On a Hermes machine, `team-kit-admin vendor --project <name>` does the same through the GitHub API and opens the pull request.
2. Add `python3 .agents/team-kit/vendor_kit.py --check .` to `scripts/check.sh`. It fails when anyone edits the vendored copy, a link breaks, or `.agents/dispatch.json` pins another commit. Changes to the kit go to the kit's repository; to take them, rerun step 1 at the new commit and commit the result.
3. Keep `.claude/settings.json` from `setup-team-kit`: it still installs the kit as plugins on engineers' own machines, where the plugin hooks run. In Claude Code a plugin skill is named `trust-ladder:follow-brief` and the vendored one `follow-brief`, so the two never clash. Pi on an engineer's machine sees each kit skill twice, from `.pi/settings.json` and from `.agents/skills/`, and keeps the first; pin `.pi/settings.json` to the vendored commit so both are the same.
4. Link the repository's own skills with `scripts/link-skills.sh` (from `setup-team-kit`) as before.

Plugin hooks do not run in a cloud session, so the compat block in AGENTS.md (from `setup-team-kit`) is what tells the agent how to read the upstream skills. Step 5 proves that `/start-here` resolves in a fresh session.

`scripts/install-kit-skills.sh` in this skill is the older route for Codex: it fetches the kit at a pinned commit into `~/.agents/skills` from the setup script. It works only while the kit's repository is public, and a vendored kit makes it unnecessary.

## 3. Wire it into each agent's cloud

- **Claude Code on the web** (also what the Claude desktop and mobile apps run). In the cloud environment's settings, set the setup script to `bash scripts/cloud-setup.sh`, set network access to what the script needs, and add environment variables and API credentials. The result is cached between sessions. For steps that must run in every session, such as starting a local database, add a `SessionStart` hook to `.claude/settings.json` that runs only when `CLAUDE_CODE_REMOTE` is `true`. Name the environment after the repository, so non-engineers can pick it without asking.
- **Codex cloud** (Codex on the web, in the ChatGPT app, and behind `@codex` on GitHub, Slack, and Linear). At chatgpt.com/codex, create an environment for the repository:
  - Setup script: `bash scripts/cloud-setup.sh`. The kit's skills come from the vendored `.agents/skills/` in the clone, so a new kit commit reaches every task as soon as its vendoring pull request merges.
  - Codex runs in two phases. The setup script has the network and the environment's secrets; the agent phase that follows has neither, by default. So the setup script must install everything, build once, and seed test data, and the verification skill must run on seeded local accounts with no API keys. Where the app needs an outside service, point it at a local fake. Turn on agent internet access only if verification cannot work without it, limited to a domain allowlist and to `GET`, `HEAD`, and `OPTIONS`.
  - The agent cannot push, open the pull request, label, or comment. When the task ends, the person presses the button to open a pull request from its diff; Codex writes the pull request from the task. CODEOWNERS still requests the right reviewer.
  - Codex reads the commands in AGENTS.md; keep them exact.
  - Also turn on the GitHub integration for the repository (it is the same one `setup-review-bots` uses for Codex review), so people can assign an issue to Codex or mention `@codex` on it. Connect Slack or Linear if the team lives there.
- **Pi.** Pi has no managed cloud. Run Pi in a remote container or VM prepared by the same script, or use pi-subagents background runs in worktrees for small fan-outs. Do not point non-engineers at Pi.

Give non-engineers access: each needs a claude.ai (or ChatGPT, for Codex) seat on the team plan, the GitHub connection for the repository, and read access to it. Branch protection (`templates/branch-protection.md`) is what keeps their pull requests safe, so turn it on first.

## 4. Decide how each surface shows its proof

For every user-facing surface, pick how a cloud session proves a change with pictures, and how those pictures reach the pull request (`references/evidence-in-prs.md`):

| Surface | Proof in a cloud container | Where the rest is verified |
|---|---|---|
| Web app | Drive it headless with `control-ui`; screenshots and traces. Codex cloud can also open its own browser and attach a screenshot to the task and its pull request | Per-pull-request preview deployments, if the team has them, give the requester a link to click |
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
4. Run a small `/request-a-change`, such as a copy change, through to a pull request. Its body must show the before and after images, and anyone on the team must be able to see them. In Codex, open the pull request from the task, and also try it from an issue: assign one to Codex, or comment `@codex use request-a-change for this issue`.

Record the time from session start to a passing check. If it is over ten minutes, cache more in the setup script.

## Report

The setup script, where it is wired in each agent, the vendored kit commit and any kit skills the repository's own kept out, whether the kit loads in a fresh session of each, the proof for each surface and where the rest is verified, the link to the test pull request, the time to a passing check, and the next steps: send non-engineers [the browser guide](https://github.com/tsnAnh/team-kit/blob/main/docs/how-to/use-the-kit-from-the-browser.md), and run `trust-audit` to see how many agents the repository can take in parallel.
