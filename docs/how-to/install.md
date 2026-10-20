# Install the kit

Install it once, in Claude Code.

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

Skills are called with a slash: `/setup-faker-kit`, `/faker-mode`. If two plugins share a name, add the plugin: `/pstack:faker-mode`.

To update: `claude plugin marketplace update faker-kit`, then restart Claude Code.

## For a whole team

A repository can make the kit appear for everyone who opens it. `/setup-faker-kit` writes these files, or copy them from [`templates/`](../../plugins/trust-ladder/templates/):

| File | Effect |
|---|---|
| `.claude/settings.json` | After a teammate trusts the folder, Claude Code registers the faker-kit marketplace and knows which plugins the project enables. Each person still runs `/plugin install trust-ladder@faker-kit` once. |
| `AGENTS.md` and `CLAUDE.md` | The instructions every agent reads, with the upstream-terms table built in. |

Cloud sessions (Claude Code on the web) install none of these plugins; `/setup-cloud-agents` vendors the kit into the repository for them.

## Check that it works

Type `/check-agent-compatibility`. Claude Code should start the check. If it does not know the skill, run `claude plugin list` and check that agent-compatibility is installed and enabled.

## Choose the model

Each person on the team uses Claude Code on Claude Opus 5.5 at max effort: `/model opus`, then `/effort max`, or for one run `claude --model opus --effort max`.

pstack's skills name a model for each role. This kit rewrote every role except code delegates to `inherit-parent`, which means "omit the model and use the one you are already running". Code delegates run on `sonnet` (Claude Sonnet 5.5), and trivial mechanical edits on `haiku` (Claude Haiku 5.5). A panel such as `/arena` or `/interrogate` still spawns one run per seat, so its opinions stay independent; they all run on your agent. Pin a role to a model with `/setup-pstack` or `/advisor <model>`, using the team's names (`claude-opus-5-5-max`).

The rewrite lives in [`upstream/model-map.json`](../../upstream/model-map.json); [the models reference](../reference/models.md) summarizes it. To change it, edit the rules, run `node scripts/sync-upstream.mjs`, and review the diff.

Next: the start page for your role in the [docs index](../README.md).
