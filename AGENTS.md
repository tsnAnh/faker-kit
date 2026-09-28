# AGENTS.md

This repository is a team kit of agent skills for Claude Code, Codex, and Pi. Read [README.md](README.md) for what it offers. This file tells an agent how to change it.

## Layout

- `plugins/<name>/` for every name in `upstream/source.json`: vendored from `cursor/plugins`. Byte-identical to upstream except the rules in `upstream/model-map.json`.
- `plugins/trust-ladder/`: this repo's own plugin. Skills, templates, lint packs, the Pi extension, and helper scripts.
- `docs/`: usage instructions. `docs/reference/skills.md` is generated.
- `scripts/`: `sync-upstream.mjs`, `validate.mjs`, and the generators and test runners.

## Never edit vendored files by hand

A hand edit under a vendored plugin fails `node scripts/sync-upstream.mjs --check`. To change a vendored file:

1. Model names: add or adjust a rule in `upstream/model-map.json`. Set `count` to the exact number of replacements.
2. A file that cannot load in Claude Code, Codex, or Pi without an edit: add a rule to `upstream/compat-patches.json` (same shape) and say why in the rule id.
3. Anything else: change the behavior in `plugins/trust-ladder/` (a wrapper skill, hook, or template), not in the vendored file.
4. Run `node scripts/sync-upstream.mjs --src <checkout of cursor/plugins>` or without `--src` to clone it.

To take a newer upstream: `node scripts/sync-upstream.mjs --ref <sha>`. Fix any rule whose count changed, then read `upstream/CHANGES.md` in the diff.

## Writing trust-ladder skills

- `name` equals the directory name: lowercase letters, digits, single hyphens.
- `description` says what the skill does and when to use it, in under 1024 characters.
- Write for an agent reading the file cold. Say which tool does what in Claude Code, Codex, and Pi only where they differ, using the vocabulary in `plugins/trust-ladder/references/compat.md`.
- Never reuse a vendored skill's name. Pi keeps only the first skill with a given name.
- Add every new skill to `docs/catalog.json`, then run `node scripts/gen-catalog.mjs`.

## Checks

Run these before you commit. CI runs the same ones.

```bash
node scripts/sync-upstream.mjs --check   # vendored plugins match upstream + rules
node scripts/validate.mjs                # manifests, frontmatter, links, catalog
node scripts/gen-catalog.mjs --check     # generated docs are current
bun test plugins/trust-ladder            # trust-ladder scripts and the Pi extension
bash scripts/test-packs.sh               # lint packs fail on bad fixtures, pass on good
claude plugin validate .                 # Claude Code marketplace schema
```
