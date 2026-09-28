---
name: setup-lint-pack
description: Install a tested lint pack into a repository (agnostic, rust, python, kotlin-android, kmp, flutter-dart, swift-ios), baseline the existing violations so CI blocks only new ones, and wire it into the single check command and CI. Use when setup-team-kit reaches static analysis, when a repository has no strict linting, or for /setup-lint-pack <stack>.
---

# Set up a lint pack

Each pack in the trust-ladder plugin's `packs/` directory is proven: its `test.sh` shows the configuration fails on a bad fixture and passes on a good one. Installing one gives a repository strict static analysis on day one without breaking the build on old code.

## 1. Choose packs

Always install `agnostic` first; it provides `scripts/check.sh` and the ratchet the other packs rely on. Then add one pack per stack found. Each pack's `pack.json` lists how to detect it under `detect`. `kmp` needs `kotlin-android` installed first.

## 2. Install

From this skill's directory, for each pack:

```bash
node scripts/install-pack.mjs <pack> --repo <repository>
```

The script copies the pack's files, appends its check commands to `.agents/check-commands`, and prints the remaining steps. It never overwrites a file that already exists with different content; it reports the file so you can merge by hand. Add `--dry-run` first on a repository with existing configuration.

For the agnostic pack, choose the optional checks:

- `--with locale-keys` when the repository has translation files. Then write `.agents/locales.json`.
- `--with no-new-comments` when the team agrees to explain code without comments. Start with `"mode": "warn"` in `.agents/no-new-comments.json` for a week if the team is unsure.

## 3. Do the printed manual steps

Merge configuration fragments (Cargo lints, Gradle snippets, `.importlinter` packages, xcconfig) exactly as `install-pack.mjs` prints them. Install the tools it lists at the versions it names.

## 4. Baseline existing violations

Run the full check once. Existing violations must not fail CI on day one, and they must not grow either. Use the pack's `baseline` method from `pack.json`:

- Native baselines (detekt, Android Lint, ktlint, SwiftLint): generate the file and commit it.
- No native baseline (clippy, ruff, Dart): mark each existing violation with a suppression whose reason is `team-kit baseline`, then add a ratchet rule that counts that marker and run `node scripts/agent-checks/ratchet.mjs update`.

Never turn a rule off to get green. Fixing a handful of violations is better than baselining them; baseline only when the count is large.

## 5. Wire CI

Add the job from the pack's `ci/github-actions.yml`, or make the existing CI run `scripts/check.sh`. One command locally and in CI means an agent can prove a change the same way CI will.

## 6. Prove it

1. `scripts/check.sh` passes on the repository.
2. Paste one of the pack's bad fixture lines into a real file. `scripts/check.sh` fails with the rule's message. Revert.
3. Add one row per pack to `.agents/corrections.tsv` at layer 2, with the proof command.

## Report

Packs installed, files copied or left for manual merge, baseline counts per rule, the CI job added, and the proof output from step 6.
