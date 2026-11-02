# AGENTS.md

This repository is faker-kit, a kit of agent skills for Claude Code. Read [README.md](README.md) for what it offers. This file tells an agent how to change it.

## Layout

- `plugins/<name>/` for every name in `upstream/source.json`: vendored from the upstream repository in `upstream/source.json`. Byte-identical to upstream except the rules in `upstream/model-map.json`, `upstream/compat-patches.json`, and `upstream/renames.json`, plus generated `.claude-plugin/plugin.json` and `agent-hooks/` files. A plugin named in `pluginSources` in `upstream/source.json` is read from that subdirectory instead of its own folder; pstack comes from upstream's generated Claude Code build, `harness/pstack/claude-code/`, so rules name its files by that path.
- `upstream/agent-hooks/hook-adapter.mjs`: runs a vendored plugin's upstream hooks from Claude Code hooks. The sync script copies it into each plugin that has a `hooks/hooks.json`.
- `plugins/trust-ladder/`: this repo's own plugin. Skills, templates, and helper scripts.
- `hermes/`: the Hermes module, this repo's own. The bootstrap, project, and chief bots' skills, profiles, guard plugin, the one `faker-kit` command, scheduled jobs, and `install.sh`. It loads only in Hermes bots, never in a coding agent, so it is not a plugin. Its skills follow the trust-ladder naming rules but need no `docs/catalog.json` entry, and must not reuse a plugin skill's name. See `hermes/README.md`.
- `docs/`: the kit's own pages (install, set up a repository, roll out, correct an agent, Hermes dispatch, the trust ladder, engineers' start page). `docs/README.md` indexes them and links into the vendored pstack guide (`plugins/pstack/docs/guide/`) and the plugin READMEs instead of restating them. `docs/reference/skills.md` is generated from `docs/catalog.json` and each SKILL.md.
- `scripts/`: `sync-upstream.mjs`, `validate.mjs`, and the generators and test runners.

## Never edit vendored files by hand

A hand edit under a vendored plugin fails `node scripts/sync-upstream.mjs --check`. To change a vendored file:

1. Model names: add or adjust a rule in `upstream/model-map.json`. Set `count` to the exact number of replacements.
2. A file that cannot load in Claude Code without an edit: add a rule to `upstream/compat-patches.json` (same shape) and say why in the rule id.
3. A name the team chose over upstream's: add a path or text rule to `upstream/renames.json`.
4. An upstream hook event the adapter does not map yet: add it to `UPSTREAM_TO_AGENT_EVENT` in the sync script and to the adapter, with a step in `scripts/e2e/kit-e2e.mjs` that runs the adapter on that event.
5. Anything else: change the behavior in `plugins/trust-ladder/` (a wrapper skill, hook, or template), not in the vendored file.
6. Run `node scripts/sync-upstream.mjs --src <checkout of the upstream repository>` or without `--src` to clone it.

To take a newer upstream: `node scripts/sync-upstream.mjs --ref <sha>`. Fix any rule whose count changed, then read `upstream/CHANGES.md` in the diff.

## Writing trust-ladder skills

- `name` equals the directory name: lowercase letters, digits, single hyphens.
- `description` says what the skill does and when to use it, in under 1024 characters.
- Write for an agent reading the file cold. Say which tool does what using the vocabulary in `plugins/trust-ladder/references/compat.md`.
- Never reuse a vendored skill's name. A project links skills by bare name, so a repeated name collides.
- If a vendored skill already covers the capability, use it (wrap it if the team needs more) instead of writing a second trust-ladder skill.
- Add every new skill to `docs/catalog.json` (layer, audience, an example prompt), then run `node scripts/gen-catalog.mjs`. `validate.mjs` fails on a skill without an entry.

## Checks

`scripts/check.sh` is the one command, locally and in CI's `check` job. It runs every line of `.agents/check-commands`: the vendored kit, the upstream sync, validate, the generated docs, the ratchet, import boundaries, ruff, mypy, and the two end-to-end runs (`hermes/e2e/run_dispatch_e2e.py` for the bot side and the guard, `scripts/e2e/kit-e2e.mjs` for the plugin side: hooks, the hook adapter, the install and migrate scripts, and the ratchet and import-boundary checks failing on a bad repository and passing a clean one). Each writes a JSON transcript to `artifacts/` and prints a digest that is the same on every run. It needs `node`, `bun`, `jq`, `git`, `python3`, and `pip install ruff mypy types-PyYAML pyyaml`.

The kit has no unit tests. Prove behavior with the e2e runs: add a step that drives the real entry point (a hook, a script, the `faker-kit` command, the guard plugin loaded as Hermes loads it) and records what it observed; never import internals and assert on them. The only other proof is `.agents/rule-fixtures.json` (real linters on good and bad inputs).

CI also runs what needs other toolchains, which a cloud session may lack:

```bash
claude plugin validate .                 # Claude Code marketplace schema
```

`check.sh` proves the code passes; the `verify-faker-kit` skill proves a user gets the behavior. Run both before reporting a change done.

## Hermes invariants

- A project bot is the long-lived coordinator chat for one repository. It decides what to launch and when to merge; no code state machine chooses for it. Code keeps only these gates: the guard's hard blocks (secret reads, repository deletion, push to main or force-push, raw `gh pr merge` and the merge API, wiping / or home), and `faker-kit merge` as the only way to land a pull request. Its gate is code: every check on the head passed and at least one check run that succeeded on it belongs to a `push` or `pull_request` workflow run of this head (a `pull_request` run must come from this pull request's branch in this repository, start after the pull request opened, and be attached to this pull request and its base branch, a `push` run must come from this repository and counts only when the head's `.github/workflows` folder equals the default branch's, and no pull request from that branch, in any state, may ever have targeted another base, and this pull request's base was never changed) of a workflow other than the review workflow and the kit-checks workflow (the repository's own GitHub Actions CI that builds and tests the pull request), `claude[bot]` approved exactly the head with `Risk: low` or `Risk: medium`, the `security` job of the review workflow's run on the head succeeded (the `security-review` status only locates the run), and no person has requested changes. `faker-kit launch` also refuses while the andon cord is pulled. The andon stop rule is an instruction in the skills, not code. No separate session verifies a pull request: the bot lands it as soon as `faker-kit merge`'s gate passes.
- A unit has no GitHub issue and no label. The brief is the cloud session's first message and the pull request is the only GitHub artifact; the bot's progress lives in its orchestrate store under `~/.hermes/faker-kit/briefs/<bot>/`.
- Integrations poll or connect outbound. A public endpoint is allowed only if it verifies the sender's signature on every request, and it only wakes the bot's own agent job: Hermes's webhook platform checks GitHub's HMAC.
- Secrets never go in the repo, logs, argv, or URLs.
- A pull request merges only on `claude[bot]`'s review of its exact head, never on text a person or agent can edit into a comment.

## Paved paths

One way for each concern. Use it; do not add a second.

- **GitHub reads in `faker-kit`:** `Gh.page` / `Gh.json` (conditional ETag requests). Never shell out to a GraphQL-backed `gh issue`/`gh pr` subcommand in code a cloud session runs; use `gh api` REST calls.
- **Starting a cloud agent:** `faker_kit_cloud.Cloud.start(repo, command, workdir)`, reached through `faker-kit launch --brief-file`. A Claude Code session's task is its first message, a process argument, so it must never hold a secret; `CloudCommand.sensitive` is the only channel for one, sent on stdin or as a follow-up message, never in argv.
- **Writing a state file:** write a sibling temp file, then `replace` it, with mode 600 for anything holding a key.
- **Time:** take `now` as a parameter (`self.now`), so tests control it; never call `time.time()` inside a decision.
- **A secret the owner provides:** the owner puts it in the bot profile's `.env` from their own terminal. Code reads it from the environment and redacts it from every log and error.

## Review guidelines

The Claude review workflow reads this section with REVIEW.md.

- Treat the rules in REVIEW.md as the review checklist, and every rule under Blocking in REVIEW.md as P1.
- Do not comment on anything `scripts/check.sh` enforces: formatting, import order, lint findings, types, generated docs.
- Hermes invariants above are blocking: a merge by any route but `faker-kit merge`, a merge gate that counts anything but the review app's review of the exact head, a launch while the andon cord is pulled, or a secret reaching argv, a log, or a URL.

## Rules and what enforces them

`/correct` keeps this table. Drop a rule once its mistake can no longer happen.

| Rule | Enforced by |
|---|---|
| Vendored plugins match upstream plus the rules | `sync-upstream.mjs --check` in `check.sh` |
| Manifests, frontmatter, links, and catalog entries are valid | `validate.mjs` in `check.sh` |
| Generated docs are current | `gen-catalog.mjs --check` in `check.sh` |
| The kit's Python lint rules hold | `ruff check` in `check.sh` |
| `hermes/lib`'s `faker_kit_andon`, `faker_kit_cloud`, `faker_kit_env`, `faker_kit_incident_sources`, and `faker_kit_machine` stay fully typed | `mypy` (strict overrides in `pyproject.toml`) in `check.sh` |
| Legacy lint and type suppressions only go down | `.agents/ratchet.json` in `check.sh` |
| `hermes/lib` never imports a command; the guard imports nothing from `lib/` or `bin/` | `.agents/import-boundaries.json` in `check.sh` |
| Secrets never reach argv or logs | the guard section of `hermes/e2e/run_dispatch_e2e.py` (secret-read blocks, run through the real guard plugin) and the one-job credential step of its runner section, both in `check.sh` |
| Merges go through `faker-kit merge`, only for a head with passing GitHub Actions CI from a `push` or `pull_request` run of this pull request in a workflow other than `claude-review.yml` and `faker-kit-checks.yml`, and no launch happens while the andon cord is pulled | `faker-kit`'s gates, the guard's hard denies on raw `gh pr merge` and pushes to main (`hard_block` in `hermes/plugin/faker_kit_guard/policy.py`), and the launch, andon, merge (a head whose only checks are the review workflow's jobs, or whose CI runs came from another event, another pull request, or another base branch, or a run from a branch that is not this pull request's, a run from a fork, a run created before the pull request opened, an unreadable branch query, timeline, or run list, or one whose branch ever had a pull request into another base or whose base was changed, is refused), and guard sections of `hermes/e2e/run_dispatch_e2e.py` in `check.sh` |
| `security-review` counts only when the `security` job of the `claude-review.yml` run it links succeeded on the head, and a pull request that touches workflows, the guard, or protected paths needs the owner | the forged-status and protected-path steps of `hermes/e2e/run_dispatch_e2e.py` in `check.sh` |
| A bot's REST calls cannot move main, write commit statuses or check runs, dismiss reviews, or link inside a briefs folder | the guard section of `hermes/e2e/run_dispatch_e2e.py` (the `guard-calls.json` corpus, run through the real guard plugin) in `check.sh` |
| Commit messages carry no AI or agent references | `scripts/check-commit-messages.mjs` in `check.sh` (promoted from STYLE.md) |
| Prefer a vendored skill to a second trust-ladder skill | review (REVIEW.md) |
| Cloud skills use `gh api` REST, not GraphQL-backed subcommands | review (REVIEW.md) |
| `faker-kit` reads GitHub only through `Gh.page` / `Gh.json`, never a GraphQL-backed `gh issue` / `gh pr` read | the zero-allowance `gh-graphql-read` rule in `.agents/ratchet.json`, proven by `.agents/rule-fixtures.json`, in `check.sh` |

## Dispatch

`.agents/dispatch.json` and `.agents/dispatch.md` configure Hermes dispatch to cloud coding agents. Cloud sessions prove their work with `scripts/check.sh` and the `verify-faker-kit` skill (the `verification_skill`).

## Cloud sessions

A Claude Code cloud session (claude.ai/code, the Claude desktop and mobile apps, `claude --cloud`, `faker-kit launch`) starts from a clone of this repository. The cloud environment's setup script installs the toolchain once and the result is cached: `scripts/cloud-setup.sh` holds the same script for a clean container test (`docker run --rm -v "$PWD:/src:ro" ubuntu:24.04 bash -c 'cp -a /src /repo && cd /repo && bash scripts/cloud-setup.sh && bash scripts/check.sh'`). The kit's skills and subagents load from the vendored copy in `.agents/faker-kit/`, linked into `.claude/skills/` and `.claude/agents/`, so `/faker-mode` and every kit skill resolve with no plugin install; plugin hooks do not run there.

| Surface | Proof in a cloud session | Where the rest is verified |
|---|---|---|
| Kit plugins, hooks, scripts, the `faker-kit` command, the guard | `scripts/check.sh`: the two e2e runs write JSON transcripts with stable digests to `artifacts/`; quote the digests and failing steps in the pull request | `claude plugin validate .` and the real-CLI plugin install step run in CI's `check` job |
| Hermes bots on a live machine | The hermes e2e transcript | The `verify-faker-kit` skill on the owner's Hermes machine |

## Learned User Preferences

- The owner wants Hermes bots to act without asking (guard.yaml `ask_owner: false`, Hermes approvals off) and unrestricted (every Hermes tool and a full shell), limited only by hard blocks in the guard's code (secret reads, repository deletion, push to main or force-push, raw `gh pr merge`, wiping / or home); keep new bot flows free of owner confirmation steps and do not add per-role tool or command limits back.
- Hermes bots talk to each other directly with `message_agent`, for questions and handoffs alike: the chief passes a new project to the bootstrap bot by message, other work straight to project bots, and a kit pin mismatch to the bootstrap bot's `faker-kit vendor --project <p>` (project bots refuse to launch while a repository's vendored kit differs from the machine's `kit_ref`, so merge pending project vendor PRs before moving `kit_ref`); `propose` is only a fallback when messaging fails, never for a question, and the owner never gets a prompt to paste into another bot's chat.
- The owner wants one GitHub token shared by all bots; setting up a new project must not ask for a new token.
- The owner wants the Hermes coordinator fully model-driven: the project bot decides launches through faker-mode's playbooks and starts Claude Code cloud sessions directly (the session's first message is its task, with no placeholder "wait for the task" message; a key goes only on stdin as a later message), keeping unit state in its own store rather than GitHub issues or labels. A GitHub pull request, review, or check event wakes it through Hermes's webhook platform, with an hourly heartbeat only as a fallback. The andon cord stays an instruction in the playbooks, as in poteto's setup, not a code gate.
- The owner wants the merge gate enforced in code, as poteto gets from GitHub's required checks: every check passes on the PR head, the Claude GitHub app (`claude[bot]`, `.github/workflows/claude-review.yml`, reviewing on every push) approved that head and rated it low or medium risk, the security review passed, and no person requested changes, with the bot merging as soon as every gate passes; Hermes bots never skip GitHub CI, though interactive Claude Code sessions may merge their own PRs on passing local checks. On GitHub Free the owner accepted this gate as a guardrail a bot with a shell and the shared token could still get around; bots never re-run, cancel, or start workflow runs, so a failed security verdict on a head sticks.
- The owner wants faker-mode's code delegates on Claude Sonnet 5.5 (`sonnet`) for features, fixes, refactors, and hard changes, and Claude Haiku 5.5 (`haiku`) for trivial mechanical edits, set through `upstream/model-map.json` rules; Claude Code's agent tool accepts only the aliases `sonnet`/`opus`/`haiku`, not version slugs. Start subagents at low or medium effort and raise effort only after a unit fails.
- The owner wants a bot to finish simple work itself instead of handing it to another bot or the owner: project bots merge their own repository's PRs when the owner or chief asks (`faker-kit merge`, gated in code), and the bootstrap bot sets up every repository in a folder with `faker-kit add-project`.
- The owner wants pstack and trust-ladder skills available in every Hermes profile, the chief and project bots included, set up by the kit's machine config.
- The kit has no unit tests at all: behavior is proven only by end-to-end runs through real entry points that write a repeatable artifact (a JSON transcript and a stable digest); never add unit tests.
- The owner wants the kit to follow poteto's workflow from the upstream plugin repository in `upstream/source.json`, with Claude Code cloud in place of the upstream editor's cloud agents and Hermes bots in place of Grok Bot, at parity with that Grok Bot setup: features it has (the chief of staff, incident alerts from Sentry, PagerDuty, Datadog, and Grafana, per-push security, Slack, andon) stay; vendored plugins and repository setup match upstream (pstack from its Claude Code build `harness/pstack/claude-code/` with its own orchestrate playbook, the full upstream editor team kit kept as team-workflows, teaching included; no lint packs, no generated `check.sh` or CI wiring, no per-language best-practices skills); kit docs reuse the vendored upstream documentation (pstack guide, plugin READMEs) instead of restating it; bloat skills, scripts, and docs are proposed to the owner for removal first, never removed unasked. An upstream plugin replaces any overlapping trust-ladder skill (repo readiness is scored by agent-compatibility's `/check-agent-compatibility`), and how many agents run at once is the owner-set `max_in_flight` in `.agents/dispatch.json`, not a score.
- The kit targets Claude Code only (Codex and Pi support dropped, so new work needs no compatibility for them), keeps the faker names (faker-mode, faker-agent) over upstream's poteto names, uses one simple `faker-kit` command (`install`, `add-project`, `vendor`, `status`, `launch`, `merge`, `issues`, `incidents`, `andon`, `runner`). The rename to faker-kit is a hard cutover: no old-name compatibility, legacy paths, or migration code in the kit; live machines and projects are cut over once by hand.
- The owner wants the chief bot to work like Grok Bot's marketplace "Engineering Lead": it owns each handoff until its PR merges, is abandoned, or the owner takes it back (not just until an agent starts), chases CI and reviews on a weekday schedule, pings the owner only when work is blocked, ready to merge, or done, batches routine updates into a digest, and steers existing work rather than starting duplicates.
- The owner wants poteto's bug-report loop: a collaborator with write access adds the `faker:fix` label to a GitHub issue, the bot starts a cloud agent that reproduces the bug and either opens a PR that fixes and closes the issue or comments that it could not reproduce it, and the normal merge gate applies; an unlabeled issue is never acted on.

## Learned Workspace Facts

- The Hermes desktop app runs Bot Chat in its own backend process (a child of Hermes.app), separate from the launchd `ai.hermes.gateway` service; after reinstalling the kit, quit and reopen the app (restarting the gateway is not enough) for guard or plugin changes to take effect.
- One Hermes process serves several profiles and names the routed profile in a ContextVar (the `hermes_constants` HERMES_HOME override); plugin code that hops threads must use `contextvars.copy_context().run`, or the profile reads as `default`.
- In a routed Hermes turn, a command's environment gets only the bot `.env` names declared in `terminal.env_passthrough`, and Hermes never passes `GH_TOKEN`/`GITHUB_TOKEN` to commands; faker-kit reads what it needs from the bot profile's `.env` itself (`hermes/lib/faker_kit_env.py`), and `gh` in bot commands falls back to the machine's keyring login.
- `hermes cron run <id>` runs the job inline (an agent job as a full model turn), not on the next scheduler pass as its help says. Hermes runs a `--no-agent` cron script only from the profile's own `scripts/` folder, with `HERMES_HOME` set to that profile but without `~/.hermes/bin` on `PATH`; faker-kit job scripts call the wrapper by its absolute path. Hermes keeps only the last 50 output files per cron job and `hermes cron runs` lists no output, so counting output files does not show new runs and a 1-minute job's notices are gone within an hour.
- Hermes has a built-in webhook platform (`hermes webhook subscribe`, `platforms.webhook` in `~/.hermes/config.yaml`) that verifies GitHub HMAC signatures and can fire an existing cron job per event (`--cron-job`); the CLI validates `--cron-job` against the default profile's jobs even with `--route-profile`, while the gateway fires the job in the routed profile at runtime.
- Hermes loads extra skill folders from `skills.external_dirs` in each profile's own `config.yaml`, per profile; the kit's machine config `skills` list sets it for the default profile and every kit bot. `install.sh` writes each bot's `SOUL.md` as its role prompt followed by the shared `hermes/profiles/OPERATING.md` operating guide (Grok Bot's ideas in the kit's own words, never the leaked prompt text), so bots pick up guide changes only after a reinstall. A long-running Bot Chat keeps the skill text it loaded earlier; skill changes reach it only if it reloads the skill.
- The Claude Code kit plugins install from the `faker-kit` marketplace; trust-ladder depends on cli-for-agent and fails to load if that plugin is disabled. `claude plugin update` skips a plugin whose version did not change (bump trust-ladder's version when it changes), so a stale plugin cache needs a reinstall. The `claude` CLI on this machine is logged in only with `CLAUDE_CONFIG_DIR=~/.claude`, and its folder-trust entries live in `~/.claude/.claude.json`; `faker-kit add-project` and every launch mark the launch checkout trusted in the config the launch uses (`$CLAUDE_CONFIG_DIR/.claude.json`, or the older `.config.json` where it exists), so a launch stops on the "trust this folder" prompt only when that config is missing or unreadable. Claude Code crashes with `EINVAL: invalid argument, kqueue` when its stdin is redirected from `/dev/tty`, so open it on the terminal it already has. Several Claude Code sessions exiting together each save their own copy of `~/.claude/.claude.json` and the last one wins, which can drop a trust entry; the next launch writes it again.
- The GitHub repository is `tsnAnh/faker-kit` (renamed from team-kit; GitHub redirects the old name), a private repo on GitHub Free: branch protection, rulesets, and GitHub auto-merge are unavailable (HTTP 403), so merge gates are enforced in `faker-kit merge` code. The owner's Mac is never a CI runner: repositories on the owner's personal account run CI and the review workflow on GitHub-hosted `ubuntu-latest`, and `TitoniumLab` organization repositories use that organization's own self-hosted runners (`gh api orgs/TitoniumLab/actions/runners`): CI and the review workflow on `[self-hosted, macOS, X64]` (the org's iMac, `caotins-iMac-2`), screenbuddy's deploy on `[self-hosted, charmeji-prod]`, because TitoniumLab's GitHub-hosted jobs fail to start on org billing. This Mac's machine config is `~/.hermes/faker-kit/machine.yaml` (`faker-kit install --config` keeps the copy that `install.sh` and `faker-kit runner up` reuse), since `.agents/hermes-machine.yaml` still has `CHANGE-ME` webhook placeholders the loader refuses.
- claude-code-action skips with "workflow validation" on a PR that adds or changes its own workflow file; it runs only once the workflow matches the default branch (the review job skips; the security job still ran). The security job gets Claude's PASS/FAIL verdict as `--json-schema` structured output from the action, not a file Claude writes, and a missing verdict fails the job. In it, Claude Code refuses a `gh api ... -f body='<long multi-line text>'` command even when it matches an allowed `:*` prefix rule; write the body to a file and pass `-F body=@<file>` with an exact allowed command. GitHub's pull request `mergeable` is null until GitHub computes it, so treat null as unknown and recheck; GitHub rejects a label description over 100 characters with HTTP 422, which fails `faker-kit add-project`'s label creation.
- The kit's bot commands run on any Python 3.9+ using the vendored pure-Python PyYAML in `hermes/vendor` (a Homebrew `python3` upgrade once broke every bot command). Homebrew's default `python3` on this Mac is 3.14 without ruff or mypy; run `scripts/check.sh` with a Python 3.12 environment that has the check tools on `PATH`. The ratchet (`scripts/agent-checks/ratchet.mjs`) counts only git-tracked files, so a suppression in a new untracked file passes locally and fails CI; `git add` new files before running `check.sh`.
- Hermes keeps each profile's chat history in `~/.hermes/profiles/<bot>/state.db` (`sessions` and `messages` tables); read it to see what a bot actually said. Bot `message_agent` may reach only this machine's faker-kit bots (the guard blocks the rest); the default Hermes agent has browser and computer-use tools and must never be messaged by a bot.
- The upstream plugin repository ships `harness/pstack/claude-code/` (an official generated Claude Code build of pstack); trust-ladder is the owner's own plugin with no upstream counterpart. `pstack:faker-agent` matches upstream's stock agent: it reads the `faker-mode` skill's `SKILL.md` by path instead of running `/faker-mode`, so its spawn prompt needs no `/faker-mode` prefix; pass the absolute `SKILL.md` path in the brief.
