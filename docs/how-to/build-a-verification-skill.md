# Build a verification skill

A verification skill lets an agent run your app the way a user does and capture proof: screenshots, recordings, terminal transcripts, logs, database rows. With it, an agent can check its own work. Without it, a person has to, and that person becomes the limit on how much the team ships.

Cursor calls its verification skill Control Glass. The pstack guide calls the generated one `/control-app`. In your repository it will be `verify-<app>`.

## Create it

```text
/create-verification-skill
```

This skill only runs when you call it by name. It:

1. Works out from the code what a user touches (web, desktop, mobile, CLI, API), how the app starts, and how an agent can drive it.
2. Writes `.cursor/skills/verify-<app>/SKILL.md` with launch, doctor, drive, evidence, and cleanup steps, plus a small CLI that scripts them. pstack calls this [building the lever](../../plugins/pstack/skills/principle-build-the-lever/SKILL.md): give agents a tool, not a page of instructions.
3. Seeds a **feature map**: one file per feature saying how to reach it, how to drive it, and what end state proves it works.
4. Runs itself once, end to end, and fixes what fails.

Then link it so every agent sees it:

```bash
bash scripts/link-skills.sh .
```

`/setup-team-kit` copies that script into the repository and adds its `--check` mode to `scripts/check.sh`, so a forgotten link fails CI.

## Mobile apps

For Android, iOS, Flutter, and KMP, point it at `control-mobile`:

```text
/create-verification-skill the app is Android and iOS. use control-mobile for the harness.
```

`control-mobile` covers emulators and simulators: installing builds, tapping by accessibility label, screenshots, screen recordings, and logs. [Verification drivers](../../plugins/trust-ladder/references/verification-drivers.md) lists the tools per platform and what a Linux cloud container cannot run (the iOS Simulator, and usually a hardware-accelerated Android emulator).

For web and Electron apps, the harness builds on cursor-team-kit's `control-ui`. For command-line tools and TUIs, `control-cli`.

## Make the CLI good

Every agent will use this CLI hundreds of times, so its quality compounds. Run `/cli-for-agents` on it: non-interactive flags, `--help` with examples, fast errors that say what to do, predictable output. Keep improving it the way you would critical infrastructure.

## Keep it current

Apps change; a stale feature map sends agents down paths that no longer exist.

```text
/maintain-verification-skill
```

It checks the map against the app, fixes what drifted, and opens at most one pull request of proven fixes. Run it daily as a routine (see [Run many agents](run-many-agents.md#routines)). Give the skill an owner, and consider an on-call rotation for it; when it breaks, every agent loses its eyes.

## Use it

You rarely call it by name. `/poteto-mode` and `/request-a-change` look for a `verify-*` skill and use it to prove each change. To prove one thing directly:

```text
/verify-this the list no longer jitters when scrolling fast
```

See [Verification is all you need](../explanation/verification-is-all-you-need.md) for why this comes first.
