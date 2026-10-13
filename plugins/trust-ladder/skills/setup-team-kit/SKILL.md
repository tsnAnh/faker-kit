---
name: setup-team-kit
description: Prepare a repository so agents can work in it unattended and anyone on the team can use the kit. Writes AGENTS.md, CLAUDE.md, REVIEW.md and STYLE.md; installs the lint pack for each stack with a baseline; wires the CI gate and Claude and Codex PR review; links project skills for Claude Code, Codex and Pi; adds team settings so teammates get the kit automatically; and starts the corrections ledger. Use for /setup-team-kit, "set this repo up for agents", or a repo with no AGENTS.md.
---

# Set up the team kit in a repository

Work on a new branch. Everything here lands as one reviewable PR. Paths below starting with `templates/`, `packs/`, or `references/` are in the trust-ladder plugin, two directories above this skill.

## 1. Inspect the repository

Answer from the files, not from the user:

- **Stacks.** `settings.gradle(.kts)` with Android plugins means kotlin-android. A `kotlin("multiplatform")` or `org.jetbrains.kotlin.multiplatform` plugin means kmp. `Cargo.toml` means rust. `pyproject.toml`, `setup.cfg`, or `requirements*.txt` means python. `pubspec.yaml` means flutter-dart. `Package.swift` or `*.xcodeproj` means swift-ios. Every repository also gets the agnostic pack.
- **Commands.** How to install, build, test, lint, and run the app. Prefer the repo's own scripts (Makefile, package scripts, Gradle tasks, `justfile`).
- **What exists.** AGENTS.md, CLAUDE.md, other editors' rule folders, `.claude/`, `.codex/`, `.pi/`, CI workflows, linters, CODEOWNERS, `.github/pull_request_template.md`.
- **Surfaces.** What a user touches: mobile app, web app, desktop app, CLI, or service.

If the repository does not build or its tests do not pass as they are, stop and report it. Setting up on a broken base teaches agents the wrong commands.

## 2. Write the instruction files

1. **AGENTS.md** from `templates/AGENTS.md`. Fill every section from step 1: one `check` command that runs everything CI runs, the layout, the boundaries, the paved paths, the never-list, and `## Review guidelines`. Delete a section rather than leave a placeholder. If AGENTS.md exists, merge: keep its content and add only the missing sections.
2. **The compat block.** Copy the text between the markers in `references/compat.md`, markers included, to the end of AGENTS.md. With it in place, the SessionStart hook and the Pi extension stop injecting it.
3. **CLAUDE.md.** If it does not exist, create it containing only `@AGENTS.md`. If it exists, add `@AGENTS.md` as its first line and move anything that applies to every agent into AGENTS.md.
4. **Rules from other tools.** Port rule files from other editors (such as `*.mdc` rules) into AGENTS.md sections, or into nested AGENTS.md files for rules scoped to one directory. Then run `correct` on each rule to see whether it can move down to a lint rule.
5. **REVIEW.md and STYLE.md** from `templates/`. Seed STYLE.md with rules the team already repeats in review. Leave it short; `style-guide-from-reviews` fills it from real review comments.

## 3. Static analysis

Run `setup-lint-pack` once per detected stack, agnostic included. Each run copies the pack, records existing violations in its baseline, and wires the checks into the `check` command and CI. Afterwards `check` must pass on the current code and fail on the pack's bad fixtures.

## 4. Review bots

Run `setup-review-bots`. It writes the Claude reviewer's prompt and walks the user through turning on Codex code review and a Claude Code review routine, so every PR gets a review from two model families whichever agent wrote it. Both run on teammates' subscriptions; the repository needs no API-key secrets.

## 5. Skills

1. Copy `scripts/link-skills.sh` from this skill to the repository's `scripts/`, then run `bash scripts/link-skills.sh .`. It links every project skill into `.claude/skills` (Claude Code) and `.agents/skills` (Codex and Pi). Add `bash scripts/link-skills.sh --check .` to `.agents/check-commands`, so a skill added later without its links fails `check`.
2. If the repository has a user-facing surface and no verification skill, recommend `/create-verification-skill` next. It is the most valuable skill the repository can have: it lets an agent prove its own work. For Android, iOS, Flutter, or KMP apps, point it at `control-mobile` and `references/verification-drivers.md`.

## 6. Team settings

So that every teammate, whichever agent they use, gets the kit without setup:

- `.claude/settings.json` from `templates/claude-settings.json`: adds this marketplace, enables the plugins, and denies destructive commands.
- `.pi/settings.json` from `templates/pi-settings.json`: pins the kit as a project package. Pi applies it after the user trusts the project.
- `.codex/config.toml` from `templates/codex-config.toml`: sandbox and approval defaults. Codex applies it after the user trusts the project. Codex users add the kit once with `codex plugin marketplace add tsnAnh/faker-kit`, then install each plugin, because Codex does not install dependencies. The one-line install loop is at the top of that template.
- If these files already name the `team-kit` marketplace or `@team-kit` plugin ids, an older setup wrote them under the kit's earlier name. From the repository root, run `node <this skill's directory>/scripts/migrate-from-team-kit.mjs .` before copying the templates, so the ids resolve for teammates who never registered team-kit, and keep the rest of each file.
- Cloud sessions do not install the plugins these files enable. `setup-cloud-agents` vendors the kit into the repository for them.
- `CODEOWNERS` and the branch-protection checklist in `templates/branch-protection.md`: the required check, resolved review conversations, and one human approval, so changes from anyone, including non-engineers, pass the same gates.

## 7. Start the records

Keep the template's `## Rules and what enforces them` table in AGENTS.md; `/correct` (pstack) fills it as mistakes get fixed. Run `trust-audit` and commit its first row to `.agents/trust-audit.tsv`. That row is the baseline the team improves on.

## 8. Verify and open the PR

1. Run `check`. It must pass.
2. Break one rule on purpose, confirm `check` fails with a useful message, then revert.
3. Run `scripts/link-skills.sh --check .`.
4. Open one PR titled `chore(agents): set up team kit`. The body lists what was added, the baseline counts per pack, the trust-audit scores, what the user must turn on after merging (the two review bots and branch protection), and the recommended next step (usually `/create-verification-skill`).
