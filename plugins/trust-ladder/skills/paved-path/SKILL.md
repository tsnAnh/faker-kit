---
name: paved-path
description: Give one concern (networking, persistence, dates, logging, navigation, feature flags, state, UI tokens) exactly one blessed way to do it. Inventories the competing patterns, picks one, migrates every caller, deletes the rest, and bans them with a lint rule whose message names the blessed API. Use when agents keep adding a second or third way to do something, when gardener finds duplicate helpers, or for /paved-path.
---

# Paved path

When a codebase offers two ways to do something, agents pick either and copy it. When it offers one, they use it. This skill turns a contested concern into a single road with the other roads removed. See `references/agent-friendly-architecture.md` in the trust-ladder plugin for why.

## 1. Inventory

1. Name the concern in one phrase, for example "formatting dates for display".
2. Find every way the codebase does it today: wrapper functions, direct library calls, duplicated helpers, copy-pasted snippets. Use `git grep`, the language's symbol search, and `node ../gardener/scripts/scan.mjs --json` for duplicate helpers.
3. Count callers per variant and note where each lives.

## 2. Pick the blessed path

Pick on design quality first: correct types, testable, fits the module boundaries, easy for a low-context agent to call. Caller count breaks ties. If none is good, design one small API first with the pstack `architect` skill.

Ask the user only when the choice is a real product or taste call that no prototype can settle.

## 3. Migrate every caller

Build the lever (`principle-build-the-lever`): write a codemod or a scripted edit instead of editing by hand, so a reviewer can rerun it. Migrate in small, verifiable steps (`principle-sequence-verifiable-units`). Each step keeps the full `check` command green.

## 4. Delete the alternatives

Delete the losing wrappers and helpers in the same change as the last caller migration (`principle-migrate-callers-then-delete-legacy-apis`). No compatibility shims.

## 5. Ban them

Run `encode-lint-rule` to forbid the raw library calls and the deleted names. The failure message says exactly what to use instead, for example "Use `AppDates.display(instant)` from `core/time`". Prove the rule fails on the old pattern and passes on the new one.

## 6. Record the path

Add one line to the `Paved paths` section of AGENTS.md: the concern, the blessed API, and what is banned. Add a row to `.agents/corrections.tsv` at layer 1.

## Report

- The concern and the variants found, with caller counts.
- The blessed API and why it won.
- The codemod or script used, and the migration PRs.
- The lint rule, with its failing and passing proof.
