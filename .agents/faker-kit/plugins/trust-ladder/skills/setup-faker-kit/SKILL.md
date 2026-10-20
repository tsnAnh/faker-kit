---
name: setup-faker-kit
description: Prepare a repository so agents can work in it unattended and anyone on the team can use the kit. Writes AGENTS.md, CLAUDE.md, REVIEW.md and STYLE.md; installs the lint pack for each stack with a baseline; wires the CI gate and the Claude review workflow; links project skills for Claude Code; adds team settings so teammates get the kit automatically; and starts the corrections ledger. Use for /setup-faker-kit, "set this repo up for agents", "install a lint pack", "set up PR review", or a repo with no AGENTS.md.
---

# Set up faker-kit in a repository

Work on a new branch. Everything here lands as one reviewable PR. Paths below starting with `templates/`, `packs/`, or `references/` are in the trust-ladder plugin, two directories above this skill.

## 1. Inspect the repository

Answer from the files, not from the user:

- **Stacks.** `settings.gradle(.kts)` with Android plugins means kotlin-android. A `kotlin("multiplatform")` or `org.jetbrains.kotlin.multiplatform` plugin means kmp. `Cargo.toml` means rust. `pyproject.toml`, `setup.cfg`, or `requirements*.txt` means python. `pubspec.yaml` means flutter-dart. `Package.swift` or `*.xcodeproj` means swift-ios. Every repository also gets the agnostic pack.
- **Commands.** How to install, build, test, lint, and run the app. Prefer the repo's own scripts (Makefile, package scripts, Gradle tasks, `justfile`).
- **What exists.** AGENTS.md, CLAUDE.md, other editors' rule folders, `.claude/`, CI workflows, linters, CODEOWNERS, `.github/pull_request_template.md`.
- **Surfaces.** What a user touches: mobile app, web app, desktop app, CLI, or service.

If the repository does not build or its tests do not pass as they are, stop and report it. Setting up on a broken base teaches agents the wrong commands.

## 2. Write the instruction files

1. **AGENTS.md** from `templates/AGENTS.md`. Fill every section from step 1: one `check` command that runs everything CI runs, the layout, the boundaries, the paved paths, the never-list, and `## Review guidelines`. Delete a section rather than leave a placeholder. If AGENTS.md exists, merge: keep its content and add only the missing sections.
2. **The compat block.** Copy the text between the markers in `references/compat.md`, markers included, to the end of AGENTS.md. With it in place, the SessionStart hook stops injecting it.
3. **CLAUDE.md.** If it does not exist, create it containing only `@AGENTS.md`. If it exists, add `@AGENTS.md` as its first line and move anything that applies to every agent into AGENTS.md.
4. **Rules from other tools.** Port rule files from other editors (such as `*.mdc` rules) into AGENTS.md sections, or into nested AGENTS.md files for rules scoped to one directory. Then run `correct` on each rule to see whether it can move down to a lint rule.
5. **REVIEW.md and STYLE.md** from `templates/`. Seed STYLE.md with rules the team already repeats in review. Leave it short; `style-guide-from-reviews` fills it from real review comments.

## 3. Static analysis: install a lint pack per stack

Each pack in the trust-ladder plugin's `packs/` directory is proven: its `test.sh` shows the configuration fails on a bad fixture and passes on a good one. Installing one gives a repository strict static analysis on day one without breaking the build on old code. Do this section once per detected stack, agnostic included, or alone when someone asks to install a pack.

1. **Choose packs.** Always install `agnostic` first; it provides `scripts/check.sh` and the ratchet the other packs rely on. Then add one pack per stack found. Each pack's `pack.json` lists how to detect it under `detect`. `kmp` needs `kotlin-android` installed first.
2. **Install.** From this skill's directory, for each pack:

   ```bash
   node scripts/install-pack.mjs <pack> --repo <repository>
   ```

   The script copies the pack's files, appends its check commands to `.agents/check-commands`, and prints the remaining steps. It never overwrites a file that already exists with different content; it reports the file so you can merge by hand. Add `--dry-run` first on a repository with existing configuration.

   For the agnostic pack, choose the optional checks:

   - `--with locale-keys` when the repository has translation files. Then write `.agents/locales.json`.
   - `--with no-new-comments` when the team agrees to explain code without comments. Start with `"mode": "warn"` in `.agents/no-new-comments.json` for a week if the team is unsure.
3. **Do the printed manual steps.** Merge configuration fragments (Cargo lints, Gradle snippets, `.importlinter` packages, xcconfig) exactly as `install-pack.mjs` prints them. Install the tools it lists at the versions it names.
4. **Baseline existing violations.** Run the full check once. Existing violations must not fail CI on day one, and they must not grow either. Use the pack's `baseline` method from `pack.json`:
   - Native baselines (detekt, Android Lint, ktlint, SwiftLint): generate the file and commit it.
   - No native baseline (clippy, ruff, Dart): mark each existing violation with a suppression whose reason is `faker-kit baseline`, then add a ratchet rule that counts that marker and run `node scripts/agent-checks/ratchet.mjs update`.

   Never turn a rule off to get green. Fixing a handful of violations is better than baselining them; baseline only when the count is large.
5. **Wire CI.** Add the job from the pack's `ci/github-actions.yml`, or make the existing CI run `scripts/check.sh`. One command locally and in CI means an agent can prove a change the same way CI will.
6. **Prove it.** `scripts/check.sh` passes on the repository. Paste one of the pack's bad fixture lines into a real file: `scripts/check.sh` must fail with the rule's message. Revert. Add one row per pack to the `## Rules and what enforces them` table in AGENTS.md: the pack, and `scripts/check.sh` as what enforces it.

To add a rule for a mistake no pack covers, run pstack's `/correct`; it writes the rule in the strongest layer that can hold it and registers its fixtures.

## 4. Review bots

Every pull request gets an automatic Claude review that follows the repository's own REVIEW.md, the AGENTS.md review guidelines, and STYLE.md. It runs on the Claude GitHub app and the team's subscription; the repository needs no `ANTHROPIC_API_KEY` secret. Never write a key into a file.

1. **Write the rules the reviewer follows.**
   - `REVIEW.md` from `templates/REVIEW.md`. Keep the blocking list short and concrete; every item says what to do instead.
   - The `## Review guidelines` section of AGENTS.md: point at REVIEW.md, say what not to comment on, and ask for every blocking rule to be treated as P1.
   - `STYLE.md` from `templates/STYLE.md`, if it does not exist yet.
2. **Install the review workflow.** Copy `templates/github/claude-review.yml` byte for byte to `.github/workflows/claude-review.yml`. It is the only review a Hermes project bot's `faker-kit merge` counts:
   - the `review` job runs on every push to a same-repository, non-draft pull request, comments on findings, and submits one formal review of the head whose body ends with a `Risk: low`, `Risk: medium`, or `Risk: high` line;
   - the `security` job reviews the diff with the `review-main-security` checklist, writes its verdict to a file inside the job, posts the `security-review` commit status on the head with a link to its run, and fails when the verdict is not `PASS`. `faker-kit merge` uses the status only to find that run, then accepts it only when the run belongs to this workflow file on this head and its `security` job succeeded, because any workflow can post a status but none can change another run's job result.

   Tell the owner what it needs, and do not write any secret yourself:
   1. The Claude GitHub app installed on the repository (github.com/apps/claude).
   2. A `CLAUDE_CODE_OAUTH_TOKEN` repository secret, from `claude setup-token`, which the owner adds on GitHub. No `ANTHROPIC_API_KEY`.
   3. A runner for `runs-on: [self-hosted, faker-kit]`: the repository listed under `ci_runners` in the machine config (`faker-kit runner up`). A repository without one changes both `runs-on` lines to `ubuntu-latest`.
   4. The owner merges the pull request that adds or changes this file. The review action refuses to run on a pull request that edits its own workflow, so that pull request gets no review, and `faker-kit merge` leaves every pull request that touches `.github/workflows/` to the owner.
3. **Make the findings count.** Review comments are not status checks, so the gate is conversation resolution: in branch protection (`templates/branch-protection.md`), require CI, a code owner's approval, and resolved conversations before merge. A `[blocking]` or P1 finding must be fixed, or answered with a reason and resolved, before the pull request can merge. Claude Code Review (Team and Enterprise) is an alternative an Owner turns on in the organization's admin settings; its check run is always neutral, so it never blocks a merge on its own.
4. **Set up triage.** Bots catch real bugs and also file noise. The pstack babysit playbook handles review comments with a fix, dismiss, or ask rubric (`references/bugbot-triage.md` in the pstack plugin; "Bugbot" there means any review bot). Copy `templates/review-bot-patterns.md` to `.agents/review-bot-patterns.md`; that file collects this repository's learned dismissal patterns so each agent does not relearn them. When the same `[blocking]` comment appears on two pull requests, it is a rule that belongs in a check. Run `/correct` on it.
5. **Prove it.** Open a throwaway pull request that breaks one REVIEW.md rule, for example a user-facing string added in one locale only. The review must flag it as `[blocking]`. Close the pull request.

## 5. Skills

1. Copy `scripts/link-skills.sh` from this skill to the repository's `scripts/`, then run `bash scripts/link-skills.sh .`. It links every project skill into `.claude/skills` and `.agents/skills`. Add `bash scripts/link-skills.sh --check .` to `.agents/check-commands`, so a skill added later without its links fails `check`.
2. If the repository has a user-facing surface and no verification skill, recommend `/create-verification-skill` next. It is the most valuable skill the repository can have: it lets an agent prove its own work. For Android, iOS, Flutter, or KMP apps, point it at `control-mobile` and `references/verification-drivers.md`.

## 6. Team settings

So that every teammate gets the kit without setup:

- `.claude/settings.json` from `templates/claude-settings.json`: adds this marketplace, enables the plugins, and denies destructive commands.
- Cloud sessions do not install the plugins this file enables. `setup-cloud-agents` vendors the kit into the repository for them.
- `CODEOWNERS` and the branch-protection checklist in `templates/branch-protection.md`: the required check, resolved review conversations, and one human approval, so changes from anyone, including non-engineers, pass the same gates.

## 7. Start the records

Keep the template's `## Rules and what enforces them` table in AGENTS.md; `/correct` (pstack) fills it as mistakes get fixed. Run `/check-agent-compatibility` and put its report in the pull request as the baseline the team improves on.

## 8. Verify and open the PR

1. Run `check`. It must pass.
2. Break one rule on purpose, confirm `check` fails with a useful message, then revert.
3. Run `scripts/link-skills.sh --check .`.
4. Open one PR titled `chore(agents): set up faker-kit`. The body lists what was added, the baseline counts per pack, the `/check-agent-compatibility` report, the CI job added, what the user must turn on after merging (the Claude GitHub app, the `CLAUDE_CODE_OAUTH_TOKEN` secret, and branch protection), links to the review from step 4.5, and the recommended next step (usually `/create-verification-skill`).
