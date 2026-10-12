# Sync the vendored plugins from upstream

The vendored plugins under `plugins/` match the upstream repository at the pinned commit, plus the rules in `upstream/`. Hermes's weekly `team-kit-upstream` job dispatches a sync unit when upstream moves.

## Sub-features

- `sync-check`: the vendored files match upstream and the rules.
- `sync-ref`: `--ref <sha>` rewrites the vendored files and `upstream/lock.json` for a new upstream commit.

## How to get to it (user POV)

- `node scripts/sync-upstream.mjs --check`, and `node scripts/sync-upstream.mjs --ref <sha>` in a sync unit.

## Driving it in this checkout

- **Check.** `node scripts/sync-upstream.mjs --check`. Proof: `vendored files match upstream <sha> + rules`.
- **A changed rule.** After editing a rule in `upstream/*.json`, run `node scripts/sync-upstream.mjs --src <checkout of upstream>` (or without `--src` to clone), then `--check`. Proof: both exit 0, and `git diff --stat plugins/` shows only the files the rule names.
- **Generated docs follow.** `node scripts/gen-catalog.mjs --check` and `node scripts/validate.mjs`. Proof: `docs are current`, `plugins OK`.

## Gotchas

- Never edit a vendored file by hand; `--check` fails on it, by design.
- A rule's `count` must equal its real number of replacements; a changed upstream changes counts.
