# Install the kit

Install it once, in the one agent you use. The kit is the same in all three; only the commands differ.

## Claude Code

In Claude Code:

```text
/plugin marketplace add tsnAnh/skills
/plugin install trust-ladder@team-kit
```

Or from a terminal:

```bash
claude plugin marketplace add tsnAnh/skills
claude plugin install trust-ladder@team-kit
```

Installing `trust-ladder` also installs the ten plugins it depends on: pstack, cursor-team-kit, thermos, orchestrate, cursor-sdk, continual-learning, cli-for-agent, agent-compatibility, advisor, and teaching. `claude plugin list` shows all eleven.

Skills are called with a slash: `/start-here`, `/poteto-mode`. If two plugins share a name, add the plugin: `/pstack:poteto-mode`.

To update: `claude plugin marketplace update team-kit`, then restart Claude Code.

## Codex

Codex reads the same marketplace file as Claude Code but does not install dependencies, so add each plugin:

```bash
codex plugin marketplace add tsnAnh/skills
for p in trust-ladder pstack cursor-team-kit thermos orchestrate cursor-sdk continual-learning cli-for-agent agent-compatibility advisor teaching; do
  codex plugin add "$p@team-kit"
done
```

`codex plugin list` shows each one as installed and enabled. Skills are called with a dollar sign: `$start-here`, `$poteto-mode`. Codex also picks a skill on its own when your request matches it.

To update: `codex plugin marketplace upgrade`, then restart Codex.

## Pi

The repository is also a Pi package:

```bash
pi install git:github.com/tsnAnh/skills
```

Pin a commit so everyone runs the same version, for example `pi install git:github.com/tsnAnh/skills@<commit sha>`. `pi list` shows the package. Skills are called as `/skill:start-here`, `/skill:poteto-mode`.

The kit's Pi extension adds what the Claude Code and Codex hooks do: it tells the agent how to read Cursor terms in the skills, and it keeps `/poteto-mode` switched on across turns until you say "exit poteto mode".

pstack's skills delegate work to subagents and ask you questions. In Pi, that needs the `pi-subagents` and `pi-ask-user` packages. If you use a setup like [pikachu](https://github.com/tsnAnh/pikachu), you already have both.

To update: `pi update --extensions`. A pinned commit stays pinned until you change it.

## For a whole team

A repository can make the kit appear for everyone who opens it. `/setup-team-kit` writes these files, or copy them from [`templates/`](../../plugins/trust-ladder/templates/):

| File | Effect |
|---|---|
| `.claude/settings.json` | After a teammate trusts the folder, Claude Code registers the team-kit marketplace and knows which plugins the project enables. Each person still runs `/plugin install trust-ladder@team-kit` once. |
| `.codex/config.toml` | Sets the team's Codex model, reasoning effort, and sandbox for this project after the teammate trusts it. The install loop above is in its header. |
| `.pi/settings.json` | Declares the kit as a pinned project package. Pi installs and loads it after the teammate trusts the project. |
| `AGENTS.md` and `CLAUDE.md` | The instructions every agent reads, with the Cursor-terms table built in. |

None of these files affect the other agents, so a repository can carry all three.

## Check that it works

Type `/start-here` (Claude Code), `$start-here` (Codex), or `/skill:start-here` (Pi). The agent should ask what you want to do. If it does not know the skill, run the list command for your agent and check that trust-ladder is installed and enabled.

Next: [choose models](models.md), then the start page for your role in the [docs index](../README.md).
