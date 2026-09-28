---
name: gardener
description: Tend the codebase the way a gardener tends weeds. Scans recent merges for smells agents copy (a third copy of the same helper, creeping lint suppressions, workaround comments, escape hatches like any, !!, unwrap), ranks them by how fast they spread, stops the bleeding with a rule, and files cleanup. Use for /gardener, a weekly or daily codebase sweep, or "what is spreading in the codebase".
---

# Gardener

Agents copy what they see. One workaround becomes a pattern within days. The gardener watches what merged recently, finds the smells that are spreading, and stops each one before it takes over.

## 1. Measure

Run the scan from this skill's directory against the repository:

```bash
node scripts/scan.mjs --since "7 days ago" --baseline "30 days ago" <repo>
```

It reports, for each smell, how many instances merged in the window, the count now, the count at the baseline, and the files with the most instances. It also lists helper names defined in three or more files, marking names that gained a copy in the window. Add `--json` to keep the result.

Then read the window's merged diffs yourself (`git log --since "7 days ago" --first-parent -p`) for what the scan cannot see: two libraries doing the same job, a second way to navigate or fetch data, or a new pattern that contradicts the paved paths in AGENTS.md.

## 2. Rank

Order the findings by spread: instances added in the window first, then growth since the baseline. A smell with zero new instances this week is debt, not an outbreak; list it and move on.

## 3. Stop the bleeding

For each of the top three spreading smells:

1. Run the `correct` skill with the smell as the bad example. It usually lands at layer 2, a lint rule with a ratchet baseline, which blocks new instances without forcing a big cleanup today.
2. For duplicated helpers or competing idioms, run the `paved-path` skill to pick the one blessed version and ban the rest.

## 4. File the cleanup

For each smell you stopped, open one issue or task: the rule that now blocks it, the baseline count, the files to clean, and the paved path to migrate to. Cleanup is then routine work any agent can pick up, because the rule proves when it is done.

## 5. Report

- The scan table, trimmed to the smells that matter.
- What you stopped, each with its rule and PR.
- The cleanup tasks you filed.
- Anything that looks like an architectural problem rather than a smell, such as the same workaround in unrelated places. Recommend a redesign per `references/agent-friendly-architecture.md` in the trust-ladder plugin.

## Running it on a schedule

A gardener works best daily or weekly. `setup-routines` can schedule this skill as a Claude Code routine or a GitHub Actions job that opens its PRs for review.
