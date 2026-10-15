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

`scripts/check.sh` is the one command, locally and in CI's `check` job. It runs every line of `.agents/check-commands`: the vendored kit, the upstream sync, validate, the generated docs and site, the ratchet, import boundaries, ruff, mypy, the trust-ladder and hook tests, and the Hermes tests (in parallel), in about two minutes. It needs `node`, `bun`, `python3`, and `pip install ruff mypy types-PyYAML pytest pytest-xdist pyyaml`.

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
- **Starting a cloud agent:** `team_kit_cloud.Cloud.start`. A Claude Code session's task is its first message, a process argument, so it must never hold a secret; `CloudCommand.sensitive` is the only channel for one, sent on stdin or as a follow-up message, never in argv.
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
| The seven shared `hermes/lib` modules stay fully typed | `mypy` (strict overrides in `pyproject.toml`) in `check.sh` |
| Legacy lint and type suppressions only go down | `.agents/ratchet.json` in `check.sh` |
| `hermes/lib` never imports a command; the guard imports nothing from `lib/` or `bin/` | `.agents/import-boundaries.json` in `check.sh` |
| Signing keys never reach argv or logs | tests in `hermes/tests/test_launch.py` and `test_e2e.py` |
| Cloud results count only with their HMAC signature | tests in `hermes/tests/test_gh.py` |
| Launch, gate, verify, and merge decisions are made in code | `team-kit-gh`'s gates, and the guard's hard denies on raw `gh pr merge` and pushes to main (`hard_block` in `hermes/plugin/team_kit_guard/policy.py`, tested in `hermes/tests/test_guard.py`) |
| Commit messages carry no AI or agent references | `scripts/check-commit-messages.mjs` in `check.sh` (promoted from STYLE.md) |
| Prefer a vendored skill to a second trust-ladder skill | review (REVIEW.md) |
| Cloud skills use `gh api` REST, not GraphQL-backed subcommands | review (REVIEW.md) |
| `team-kit-gh` reads GitHub only through `Gh.api_page` / `Gh.api_json`, never a GraphQL-backed `gh issue` / `gh pr` read | the zero-allowance `gh-graphql-read` rule in `.agents/ratchet.json`, proven by `.agents/rule-fixtures.json`, in `check.sh` |

## Dispatch

`.agents/dispatch.json` and `.agents/dispatch.md` configure Hermes dispatch to cloud coding agents. Units prove their work with `scripts/check.sh` and the `verify-team-kit` skill (the `verification_skill`).

## Learned User Preferences

- The owner wants Hermes bots to act without asking (guard.yaml `ask_owner: false`, Hermes approvals off); keep new bot flows free of owner confirmation steps unless a Hermes invariant requires one.
- Hermes bots talk to each other directly with `message_agent`, for questions and handoffs alike: the chief passes a new project to the bootstrap bot with `team-kit-chief new-project`, other work straight to project bots, and a kit pin mismatch to the bootstrap bot's `team-kit-admin vendor --project <p>` (which moves `kit_ref` in `.agents/dispatch.json`); `propose` is only a fallback when messaging fails, never for a question, and the owner never gets a prompt to paste into another bot's chat.
- The owner wants one GitHub token shared by all bots; setting up a new project must not ask for a new token.
- The owner wants Hermes dispatch event-driven: a GitHub event such as a new issue should launch Claude Code as soon as possible, with polling only as a fallback.
- The owner wants code review done by an outside service (the Claude GitHub app, `.github/workflows/claude-review.yml`), not Hermes review or verifier sessions, and low-risk PRs merged automatically once that app approves (`.agents/dispatch.json`: review and verifier backends `none`, `review_app` `claude[bot]`, `auto_merge` `low`, `security_backend` `none`); no Claude Code cloud session reviews changes.
- The owner wants a dispatched Claude Code session's first message to be its task, with no placeholder "wait for the task" message; a signing key still goes only on stdin as a later message.
- The owner wants faker-mode's code delegates on Claude Sonnet 5.5 (`sonnet`) for features, fixes, refactors, and hard changes, and Claude Haiku 5.5 (`haiku`) for trivial mechanical edits, set through `upstream/model-map.json` rules; Claude Code's agent tool accepts only the aliases `sonnet`/`opus`/`haiku`, not version slugs.
- The owner wants a bot to finish simple work itself instead of handing it to another bot or the owner: project bots merge and close their own repository's PRs when the owner or chief asks (`team-kit-gh merge`/`close`, gated in code), the bootstrap bot sets up every repository in a folder (`team-kit-admin discover`/`adopt`), and with `auto_merge` `low` a project bot's tick merges the kit PR whose head is the commit `team-kit-admin vendor` made.
- The owner wants pstack and trust-ladder skills available in every Hermes profile, the chief and project bots included, set up by the kit's machine config.
- Interactive Claude Code sessions may merge their own PRs on passing local checks without GitHub CI, but Hermes bots never skip CI: their merge gate keeps requiring GitHub checks to pass.
- The owner wants the kit at parity with the upstream team's Grok Bot setup: features that setup has (incidents, per-push security, verifiers, Slack, andon) stay, and other features judged over-engineered are proposed to the owner for removal first, never removed unasked.

## Learned Workspace Facts

- The Hermes desktop app runs Bot Chat in its own backend process (a child of Hermes.app), separate from the launchd `ai.hermes.gateway` service; after reinstalling the kit, quit and reopen the app (restarting the gateway is not enough) for guard or plugin changes to take effect.
- One Hermes process serves several profiles and names the routed profile in a ContextVar (the `hermes_constants` HERMES_HOME override); plugin code that hops threads must use `contextvars.copy_context().run`, or the profile reads as `default`.
- In a routed Hermes turn, a command's environment gets only the bot `.env` names declared in `terminal.env_passthrough`, and Hermes never passes `GH_TOKEN`/`GITHUB_TOKEN` to commands; team-kit commands read what they need from the bot profile's `.env` themselves (`hermes/lib/team_kit_env.py`), and `gh` in bot commands falls back to the machine's keyring login.
- `hermes cron run <id>` runs the job inline (an agent job as a full model turn), not on the next scheduler pass as its help says. Hermes runs a `--no-agent` cron script only from the profile's own `scripts/` folder, with `HERMES_HOME` set to that profile but without `~/.hermes/bin` on `PATH`; team-kit job scripts call the wrapper by its absolute path. Hermes keeps only the last 50 output files per cron job and `hermes cron runs` lists no output, so counting output files does not show new runs and a 1-minute tick's notices are gone within an hour; an issue's label events show when a gate happened.
- Hermes has a built-in webhook platform (`hermes webhook subscribe`, `platforms.webhook` in `~/.hermes/config.yaml`) that verifies GitHub HMAC signatures and can fire an existing cron job per event (`--cron-job`); the CLI validates `--cron-job` against the default profile's jobs even with `--route-profile`, while the gateway fires the job in the routed profile at runtime.
- Hermes loads extra skill folders from `skills.external_dirs` in each profile's own `config.yaml`, per profile; the kit's machine config `skills` list sets it for the default profile and every kit bot. A long-running Bot Chat keeps the skill text it loaded earlier; skill changes reach it only if it reloads the skill.
- The Claude Code kit plugins install from the `team-kit` marketplace; trust-ladder depends on cli-for-agent and fails to load if that plugin is disabled. `claude plugin update` skips a plugin whose version did not change (trust-ladder stays 0.1.0), so a stale plugin cache needs a reinstall. The `claude` CLI on this machine is logged in only with `CLAUDE_CONFIG_DIR=~/.claude`, and its folder-trust entries live in `~/.claude/.claude.json`; a launch without that variable, or from a checkout path not trusted before, stops on the "trust this folder" prompt.
- The GitHub repository is `tsnAnh/faker-kit` (renamed from team-kit; GitHub redirects the old name), a private repo on GitHub Free: branch protection, rulesets, and GitHub auto-merge are unavailable (HTTP 403), so merge gates are enforced in `team-kit-gh` code. GitHub-hosted Actions runners fail on this account for billing, so CI for the kit and project repos runs on self-hosted runners on the Hermes machine (`team-kit-runner`, `runs-on: [self-hosted, team-kit]`, one fresh Ubuntu container per job); only `docs-site.yml` stays on `ubuntu-latest` because it builds a Docker image. This Mac's runner config is `~/.hermes/team-kit/machine.yaml`, since `.agents/hermes-machine.yaml` still has `CHANGE-ME` webhook placeholders the loader refuses.
- claude-code-action skips with "workflow validation" on a PR that adds or changes its own workflow file; it runs only once the workflow matches the default branch. In it, Claude Code refuses a `gh api ... -f body='<long multi-line text>'` command even when it matches an allowed `:*` prefix rule; write the body to a file and pass `-F body=@<file>` with an exact allowed command.
- The kit's bot commands run on any Python 3.9+ using the vendored pure-Python PyYAML in `hermes/vendor` (a Homebrew `python3` upgrade once broke every bot command). Homebrew's default `python3` on this Mac is 3.14 without ruff, mypy, or pytest; run `scripts/check.sh` with a Python 3.12 environment that has the check tools on `PATH`. The ratchet (`scripts/agent-checks/ratchet.mjs`) counts only git-tracked files, so a suppression in a new untracked file passes locally and fails CI; `git add` new files before running `check.sh`.
- GitHub's pull request `mergeable` is null until GitHub computes it, so a just-read PR can show no conflict that appears on a later read; treat null as unknown and recheck. GitHub caps a label description at 100 characters and rejects a longer one with HTTP 422, which fails `team-kit-admin add-project`'s label creation.
- Hermes keeps each profile's chat history in `~/.hermes/profiles/<bot>/state.db` (`sessions` and `messages` tables); read it to see what a bot actually said. Bot `message_agent` may reach only this machine's team-kit bots (the guard blocks the rest); the default Hermes agent has browser and computer-use tools and must never be messaged by a bot.
