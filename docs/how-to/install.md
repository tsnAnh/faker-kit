# Install the kit

Install it once, in the one agent you use. The kit is the same in all three; only the commands differ.

## Claude Code

In Claude Code:

```text
/plugin marketplace add tsnAnh/faker-kit
/plugin install trust-ladder@faker-kit
```

Or from a terminal:

```bash
claude plugin marketplace add tsnAnh/faker-kit
claude plugin install trust-ladder@faker-kit
```

Installing `trust-ladder` also installs the eight plugins it depends on: pstack, team-workflows, thermos, continual-learning, cli-for-agent, agent-compatibility, advisor, and teaching. `claude plugin list` shows all nine.

Skills are called with a slash: `/start-here`, `/faker-mode`. If two plugins share a name, add the plugin: `/pstack:faker-mode`.

To update: `claude plugin marketplace update faker-kit`, then restart Claude Code.

## Codex

Codex reads the same marketplace file as Claude Code but does not install dependencies, so add each plugin:

```bash
codex plugin marketplace add tsnAnh/faker-kit
for p in trust-ladder pstack team-workflows thermos continual-learning cli-for-agent agent-compatibility advisor teaching; do
  codex plugin add "$p@faker-kit"
done
```

`codex plugin list` shows each one as installed and enabled. Skills are called with a dollar sign: `$start-here`, `$faker-mode`. Codex also picks a skill on its own when your request matches it.

Codex runs a plugin's hooks only after you trust them. At the first start it says hooks need review: type `/hooks` and trust the faker-kit ones. trust-ladder's add the upstream-terms table and keep `$faker-mode` pinned; advisor's and continual-learning's run those plugins' reminders. Review them again after an update changes them.

To update: `codex plugin marketplace upgrade`, then restart Codex.

## Pi

The repository is also a Pi package:

```bash
pi install git:github.com/tsnAnh/faker-kit
```

Pin a commit so everyone runs the same version, for example `pi install git:github.com/tsnAnh/faker-kit@<commit sha>`. `pi list` shows the package. Skills are called as `/skill:start-here`, `/skill:faker-mode`.

The kit's Pi extension adds what the Claude Code and Codex hooks do: it tells the agent how to read the upstream terms in the skills, and it keeps `/faker-mode` switched on across turns until you say "exit faker mode".

pstack's skills delegate work to subagents and ask you questions. In Pi, that needs the `pi-subagents` and `pi-ask-user` packages. If you use a setup like [pikachu](https://github.com/tsnAnh/pikachu), you already have both.

To update: `pi update --extensions`. A pinned commit stays pinned until you change it.

## For a whole team

A repository can make the kit appear for everyone who opens it. `/setup-team-kit` writes these files, or copy them from [`templates/`](../../plugins/trust-ladder/templates/):

| File | Effect |
|---|---|
| `.claude/settings.json` | After a teammate trusts the folder, Claude Code registers the faker-kit marketplace and knows which plugins the project enables. Each person still runs `/plugin install trust-ladder@faker-kit` once. |
| `.codex/config.toml` | Sets the team's Codex model, reasoning effort, and sandbox for this project after the teammate trusts it. The install loop above is in its header. |
| `.pi/settings.json` | Declares the kit as a pinned project package. Pi installs and loads it after the teammate trusts the project. |
| `AGENTS.md` and `CLAUDE.md` | The instructions every agent reads, with the upstream-terms table built in. |

None of these files affect the other agents, so a repository can carry all three. Cloud sessions (Claude Code on the web, Codex cloud) install none of these plugins; [`/setup-cloud-agents`](use-the-kit-from-the-browser.md) vendors the kit into the repository for them.

## Move from the team-kit marketplace

The kit used to be called team-kit, and its repository was renamed to `tsnAnh/faker-kit`. An install made under the old name keeps working, because GitHub redirects the old repository name. `claude plugin marketplace update team-kit` still updates it. Moving is optional.

If you move, remove the old registration first. With both registered, every plugin loads twice: `trust-ladder@team-kit` and `trust-ladder@faker-kit` are both enabled.

**Claude Code.** Removing the marketplace uninstalls its plugins. As the CLI warns, it also deletes their saved options and data, so you enter plugin settings again afterwards.

```bash
claude plugin marketplace remove team-kit
claude plugin marketplace add tsnAnh/faker-kit
claude plugin install trust-ladder@faker-kit
```

**Codex.** Removing only the marketplace leaves each `[plugins."<plugin>@team-kit"]` entry in `config.toml` and the plugins in the cache. Remove the plugins first.

```bash
for p in trust-ladder pstack team-workflows thermos continual-learning cli-for-agent agent-compatibility advisor teaching; do
  codex plugin remove "$p@team-kit"
done
codex plugin marketplace remove team-kit
codex plugin marketplace add tsnAnh/faker-kit
for p in trust-ladder pstack team-workflows thermos continual-learning cli-for-agent agent-compatibility advisor teaching; do
  codex plugin add "$p@faker-kit"
done
```

**Pi.** With both packages installed, Pi loads two copies of the extension and keeps only the first skill of each name. Remove the old one, with its `@<commit sha>` if you pinned it.

```bash
pi remove git:github.com/tsnAnh/team-kit   # legacy team-kit source
pi install git:github.com/tsnAnh/faker-kit@<commit sha>
```

**A repository set up earlier.** An older `/setup-team-kit` wrote the `team-kit` marketplace and `@team-kit` plugin ids into `.claude/settings.json`. Teammates who registered team-kit before the rename keep working. A teammate who never did does not: Claude Code registers the marketplace under the name in its manifest, now `faker-kit`, so `/plugin install trust-ladder@team-kit` fails with `Plugin "trust-ladder" not found in marketplace "team-kit"`. Move the repository with one command from a checkout of the kit, then commit the result:

```bash
node <kit checkout>/plugins/trust-ladder/skills/setup-team-kit/scripts/migrate-from-team-kit.mjs <repository>
```

It renames the marketplace entry and every `@team-kit` id in `.claude/settings.json`, moves a team-kit package in `.pi/settings.json` to faker-kit at the same commit, and updates the install commands in `.codex/config.toml`'s comments. Everything else in those files stays. A second run changes nothing, and `--check` reports without writing. If an id is already enabled under both names, it keeps the faker-kit one. Then ask teammates who registered team-kit to move as above, so they do not load two copies.

**Hermes.** Re-run the installer from a faker-kit checkout. Nothing moves. See [Upgrading from team-kit](../../hermes/README.md#upgrading-from-team-kit).

## Check that it works

Type `/start-here` (Claude Code), `$start-here` (Codex), or `/skill:start-here` (Pi). The agent should ask what you want to do. If it does not know the skill, run the list command for your agent and check that trust-ladder is installed and enabled.

Next: [choose models](models.md), then the start page for your role in the [docs index](../README.md).
