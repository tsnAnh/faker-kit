# AGENTS.md

This repository is a team kit of agent skills for Claude Code, Codex, and Pi. Read [README.md](README.md) for what it offers. This file tells an agent how to change it.

## Layout

- `plugins/<name>/` for every name in `upstream/source.json`: vendored from the upstream repository in `upstream/source.json`. Byte-identical to upstream except the rules in `upstream/model-map.json`, `upstream/compat-patches.json`, and `upstream/renames.json`, plus generated `.claude-plugin/plugin.json` and `agent-hooks/` files.
- `upstream/agent-hooks/hook-adapter.mjs`: runs a vendored plugin's upstream hooks from Claude Code and Codex hooks. The sync script copies it into each plugin that has a `hooks/hooks.json`.
- `plugins/trust-ladder/`: this repo's own plugin. Skills, templates, lint packs, the Pi extension, and helper scripts.
- `hermes/`: the Hermes module, this repo's own. The bootstrap and project bots' skills, profiles, guard plugin, `team-kit-gh`, `team-kit-admin`, scheduled jobs, and `install.sh`. It loads only in Hermes bots, never in a coding agent, so it is not a plugin. Its skills follow the trust-ladder naming rules but need no `docs/catalog.json` entry, and must not reuse a plugin skill's name. See `hermes/README.md`.
- `docs/`: usage instructions. `docs/reference/{skills,coverage,playbooks,lint-packs}.md` are generated from `docs/catalog.json`, each SKILL.md, faker-mode's playbook list, and each `pack.json`.
- `site/`: the Mintlify docs site, deployed to the team's k3s server by `.github/workflows/docs-site.yml` using the image and manifests in `deploy/` (setup: `deploy/README.md`). The home page, `quickstart/`, `guides/`, and `docs.json` are hand-written. `node scripts/gen-site.mjs` generates `how-to/`, `explanation/`, and `reference/` from `docs/` (gitignored). Edit `docs/`, never the generated copies. A new `docs/` page needs an entry in `site/docs.json`. Preview with `cd site && npx mint dev`.
- `scripts/`: `sync-upstream.mjs`, `validate.mjs`, and the generators and test runners.

## Never edit vendored files by hand

A hand edit under a vendored plugin fails `node scripts/sync-upstream.mjs --check`. To change a vendored file:

1. Model names: add or adjust a rule in `upstream/model-map.json`. Set `count` to the exact number of replacements.
2. A file that cannot load in Claude Code, Codex, or Pi without an edit: add a rule to `upstream/compat-patches.json` (same shape) and say why in the rule id.
3. A name the team chose over upstream's: add a path or text rule to `upstream/renames.json`.
4. An upstream hook event the adapter does not map yet: add it to `UPSTREAM_TO_AGENT_EVENT` in the sync script and to the adapter, with a test in `upstream/agent-hooks/`.
5. Anything else: change the behavior in `plugins/trust-ladder/` (a wrapper skill, hook, or template), not in the vendored file.
6. Run `node scripts/sync-upstream.mjs --src <checkout of the upstream repository>` or without `--src` to clone it.

To take a newer upstream: `node scripts/sync-upstream.mjs --ref <sha>`. Fix any rule whose count changed, then read `upstream/CHANGES.md` in the diff.

## Writing trust-ladder skills

- `name` equals the directory name: lowercase letters, digits, single hyphens.
- `description` says what the skill does and when to use it, in under 1024 characters.
- Write for an agent reading the file cold. Say which tool does what in Claude Code, Codex, and Pi only where they differ, using the vocabulary in `plugins/trust-ladder/references/compat.md`.
- Never reuse a vendored skill's name. Pi keeps only the first skill with a given name.
- Add every new skill to `docs/catalog.json` (layer, audience, an example prompt), then run `node scripts/gen-catalog.mjs`. `validate.mjs` fails on a skill without an entry.

## Checks

Run these before you commit. CI runs the same ones.

```bash
node scripts/sync-upstream.mjs --check   # vendored plugins match upstream + rules
node scripts/validate.mjs                # manifests, frontmatter, links, catalog
node scripts/gen-catalog.mjs --check     # generated docs are current
node scripts/gen-site.mjs                # docs site pages, links, and navigation resolve
bun test plugins/trust-ladder            # trust-ladder scripts and the Pi extension
(cd plugins/trust-ladder/skills/orchestrate/scripts && bun install && bun test)   # orchestrate engine and runtimes
bun test upstream/agent-hooks            # upstream hooks run in Claude Code and Codex
python -m pytest hermes                  # Hermes guard, team-kit-gh, team-kit-admin, install
bash scripts/test-packs.sh               # lint packs fail on bad fixtures, pass on good
claude plugin validate .                 # Claude Code marketplace schema
```

## Dispatch

`.agents/dispatch.json` and `.agents/dispatch.md` configure Hermes dispatch to cloud coding agents. `scripts/check.sh` is the single fast check (vendored kit, sync, validate, catalog, site, trust-ladder and hook tests); CI runs it as the `check` job. The slower checks above (orchestrate engine, Hermes pytest, lint packs, `claude plugin validate`) run in the other CI jobs, and a cloud session may lack their toolchains.
