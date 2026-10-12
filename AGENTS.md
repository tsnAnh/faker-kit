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
- If a vendored skill already covers the capability, use it (wrap it if the team needs more) instead of writing a second trust-ladder skill.
- Add every new skill to `docs/catalog.json` (layer, audience, an example prompt), then run `node scripts/gen-catalog.mjs`. `validate.mjs` fails on a skill without an entry.

## Checks

`scripts/check.sh` is the one command, locally and in CI's `check` job. It runs every line of `.agents/check-commands`: the vendored kit, the upstream sync, validate, the generated docs and site, the ratchet, import boundaries, ruff, mypy, the trust-ladder and hook tests, and the Hermes tests, in about three minutes. It needs `node`, `bun`, `python3`, and `pip install ruff mypy types-PyYAML pytest pyyaml`.

CI also runs what needs other toolchains, which a cloud session may lack:

```bash
(cd plugins/trust-ladder/skills/orchestrate/scripts && bun install && bun test)   # orchestrate engine and runtimes
bash scripts/test-packs.sh               # lint packs fail on bad fixtures, pass on good
claude plugin validate .                 # Claude Code marketplace schema
```

`check.sh` proves the code passes; the `verify-team-kit` skill proves a user gets the behavior. Run both before reporting a change done.

## Hermes invariants

- `team-kit-gh tick` and every scheduled job decide in code. No model chooses what launches or gates.
- Integrations poll or connect outbound. A public endpoint is allowed only if it verifies the sender's signature on every request, and it never runs a model-chosen action.
- Secrets never go in the repo, logs, argv, or URLs.
- Cloud agent results stay HMAC-signed.

## Paved paths

One way for each concern. Use it; do not add a second.

- **GitHub reads in `team-kit-gh`:** `Gh.api_page` / `Gh.api_json` (conditional ETag requests). Never shell out to a GraphQL-backed `gh issue`/`gh pr` subcommand in code a cloud session runs; use `gh api` REST calls.
- **Starting a cloud agent:** `team_kit_cloud.Cloud.start`. Prompts go on stdin or as a follow-up message, never in argv.
- **Writing a state file:** write a sibling temp file, then `replace` it, with mode 600 for anything holding a key.
- **Time:** take `now` as a parameter (`self.now`), so tests control it; never call `time.time()` inside a decision.
- **A secret the owner provides:** the owner puts it in the bot profile's `.env` from their own terminal. Code reads it from the environment and redacts it from every log and error.

## Review guidelines

Codex code review reads this section. The Claude review routine reads it with REVIEW.md.

- Treat the rules in REVIEW.md as the review checklist, and every rule under Blocking in REVIEW.md as P1.
- Do not comment on anything `scripts/check.sh` enforces: formatting, import order, lint findings, types, generated docs.
- Hermes invariants above are blocking: a model choosing a launch, a secret reaching argv, a log, or a URL, or an unsigned cloud result.

## Rules and what enforces them

`/correct` keeps this table. Drop a rule once its mistake can no longer happen.

| Rule | Enforced by |
|---|---|
| Vendored plugins match upstream plus the rules | `sync-upstream.mjs --check` in `check.sh` |
| Manifests, frontmatter, links, and catalog entries are valid | `validate.mjs` in `check.sh` |
| Generated docs and site pages are current | `gen-catalog.mjs --check`, `gen-site.mjs` in `check.sh` |
| The python lint pack's rules hold for the kit's Python | `ruff check` in `check.sh` |
| The six shared `hermes/lib` modules stay fully typed | `mypy` (strict overrides in `pyproject.toml`) in `check.sh` |
| Legacy lint and type suppressions only go down | `.agents/ratchet.json` in `check.sh` |
| `hermes/lib` never imports a command; the guard imports nothing from `lib/` or `bin/` | `.agents/import-boundaries.json` in `check.sh` |
| Prompts and signing keys never reach argv or logs | tests in `hermes/tests/test_launch.py` and `test_e2e.py` |
| Cloud results count only with their HMAC signature | tests in `hermes/tests/test_gh.py` |
| Launch, gate, verify, and merge decisions are made in code | the architecture of `team-kit-gh` (the bot's only tool), and review |
| Commit messages carry no AI or agent references | `scripts/check-commit-messages.mjs` in `check.sh` (promoted from STYLE.md) |
| Prefer a vendored skill to a second trust-ladder skill | review (REVIEW.md) |
| Cloud skills use `gh api` REST, not GraphQL-backed subcommands | review (REVIEW.md) |

## Dispatch

`.agents/dispatch.json` and `.agents/dispatch.md` configure Hermes dispatch to cloud coding agents. Units prove their work with `scripts/check.sh` and the `verify-team-kit` skill (the `verification_skill`).

## Learned User Preferences

## Learned Workspace Facts

- The Hermes desktop app runs Bot Chat in its own backend process (a child of Hermes.app), separate from the launchd `ai.hermes.gateway` service; after reinstalling the kit, quit and reopen the app (restarting the gateway is not enough) for guard or plugin changes to take effect.
- One Hermes process serves several profiles and names the routed profile in a ContextVar (the `hermes_constants` HERMES_HOME override); plugin code that hops threads must use `contextvars.copy_context().run`, or the profile reads as `default`.
