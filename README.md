# faker-kit

faker-kit is a kit for Claude Code that makes coding agents trustworthy enough to run many at once.

It packages eight open-source agent plugins (Lauren Tan's pstack, team-workflows, thermos, and five more), unchanged except for model names and a few renames, and adds `trust-ladder`: skills that turn every correction you give an agent into a lasting fix, review and security triage, and a Hermes module that dispatches work to Claude Code cloud sessions.

## Install

In Claude Code:

```text
/plugin marketplace add tsnAnh/faker-kit
/plugin install trust-ladder@faker-kit
```

**Hermes**, to dispatch work to cloud agents from Hermes Desktop. From a clone of this repository, run `bash hermes/install.sh --ref <commit> --bootstrap-bot <name>`, then ask the bootstrap bot for a project. See [Dispatch work with Hermes](docs/how-to/dispatch-with-hermes.md).

Details, team-wide setup, and updating: [Install the kit](docs/how-to/install.md). Engineers start with [your first hour](docs/start/engineers.md).

## The trust ladder

Every time you correct an agent, `/correct` puts the fix in the strongest layer that can hold it, so no agent needs the same correction again:

| Layer | Holds a rule as | Kit skills and tools |
|---|---|---|
| 1. Codebase | Types, boundaries, one blessed way per concern | Language best-practice skills, `/architect` |
| 2. Static analysis | Lint rules, compiler settings, CI checks, ratchets | `/correct`, which adds a targeted lint to the repository's own CI |
| 3. Rules and review bots | AGENTS.md; Claude review on every PR | `setup-faker-kit`, `/thermos`, `/interrogate` |
| 4. Skills | Procedures, above all a verification skill that runs the app and proves changes | `/create-verification-skill`, `control-mobile`, `/faker-mode` and its playbooks |
| 5. Style guide | STYLE.md, enforced by human reviewers until it can climb | `style-guide-from-reviews` |

`/check-agent-compatibility` checks how ready a repository is for agents, with the evidence behind each finding. [Why it works](docs/explanation/trust-ladder.md).

## What is in the repository

| Path | Contents |
|---|---|
| `plugins/trust-ladder/` | This kit's plugin: skills, templates, and hooks |
| `plugins/pstack/` and seven others | Vendored from the MIT-licensed upstream repository named in `upstream/source.json`, byte-identical except model names and the `faker-mode` rename, without the `make-bot-ui` skill (it only drives the upstream editor's bot), plus generated manifests and hook files ([what changed](upstream/CHANGES.md)) |
| `hermes/` | The Hermes module: bots' skills, the guard, the `faker-kit` command, and `install.sh` |
| `docs/` | The kit's own pages, indexed in [docs/README.md](docs/README.md), with links into the pstack guide |
| `upstream/` | The pinned upstream commit, the model map, the team's renames, the hook adapter, and the change list |
| `scripts/` | Sync, validation, catalog generation, the plugin-side e2e run (`e2e/kit-e2e.mjs`), and the checks CI runs |

Every skill, with who it is for and a prompt to try: [docs/reference/skills.md](docs/reference/skills.md).

## License

MIT. The vendored plugins keep their own MIT licenses and authors; see [NOTICE.md](NOTICE.md).
