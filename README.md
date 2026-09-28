# team-kit

A team kit for Claude Code, Codex, and Pi that makes coding agents trustworthy enough to run many at once, and safe enough for anyone on the team to use.

It packages eight open-source agent plugins (Lauren Tan's pstack, team-workflows, thermos, and five more), unchanged except for model names and a few renames, and adds `trust-ladder`: skills that turn every correction you give an agent into a lasting fix, lint packs for seven stacks, review bots, routines, and plain-language skills for designers, marketers, and founders.

## Install

Pick the one agent you use.

**Claude Code**

```text
/plugin marketplace add tsnAnh/team-kit
/plugin install trust-ladder@team-kit
```

**Codex**

```bash
codex plugin marketplace add tsnAnh/team-kit
for p in trust-ladder pstack team-workflows thermos continual-learning cli-for-agent agent-compatibility advisor teaching; do codex plugin add "$p@team-kit"; done
```

**Pi**

```bash
pi install git:github.com/tsnAnh/team-kit
```

Then type `/start-here` (Codex: `$start-here`, Pi: `/skill:start-here`). Details, team-wide setup, and updating: [Install the kit](docs/how-to/install.md).

## Start with your role

| You are | Read |
|---|---|
| An engineer | [Start here: engineers](docs/start/engineers.md) |
| A designer | [Start here: designers](docs/start/designers.md) |
| In product or marketing | [Start here: product and marketing](docs/start/product-and-marketing.md) |
| A founder or lead | [Start here: founders and leads](docs/start/founders-and-leads.md) |

Designers, marketers, and founders need nothing installed once an engineer has run `/setup-cloud-agents`: [use the kit from a browser or your phone](docs/how-to/use-the-kit-from-the-browser.md).

All documentation: [docs/](docs/README.md).

## The trust ladder

Every time you correct an agent, `/correct` puts the fix in the strongest layer that can hold it, so no agent needs the same correction again:

| Layer | Holds a rule as | Kit skills and tools |
|---|---|---|
| 1. Codebase | Types, boundaries, one blessed way per concern | `paved-path`, `gardener`, language best-practice skills, `/architect` |
| 2. Static analysis | Lint rules, compiler settings, CI checks, ratchets | [Lint packs](docs/reference/lint-packs.md) for Kotlin/Android, KMP, Rust, Python, Flutter/Dart, Swift/iOS, and any stack; `encode-lint-rule` |
| 3. Rules and review bots | AGENTS.md; Claude and Codex review on every PR | `setup-team-kit`, `setup-review-bots`, `/thermos`, `/interrogate` |
| 4. Skills | Procedures, above all a verification skill that runs the app and proves changes | `/create-verification-skill`, `control-mobile`, `/faker-mode` and its playbooks |
| 5. Style guide | STYLE.md, enforced by human reviewers until it can climb | `style-guide-from-reviews` |

`/trust-audit` scores a repository on each layer and says how many agents it can safely take. [Why it works](docs/explanation/trust-ladder.md).

## What is in the repository

| Path | Contents |
|---|---|
| `plugins/trust-ladder/` | This kit's plugin: 20 skills, templates, lint packs, hooks, and the Pi extension |
| `plugins/pstack/` and seven others | Vendored from the MIT-licensed upstream repository named in `upstream/source.json`, byte-identical except model names and the `faker-mode` rename, without the `make-bot-ui` skill (it only drives the upstream editor's bot), plus generated manifests and hook files ([what changed](upstream/CHANGES.md)) |
| `docs/` | Usage instructions for every role |
| `upstream/` | The pinned upstream commit, the model map, the team's renames, the hook adapter, and the change list |
| `scripts/` | Sync, validation, catalog generation, and the test runners CI uses |

Every skill, with who it is for and a prompt to try: [docs/reference/skills.md](docs/reference/skills.md). Where everything from the talk and the pstack guide posts lives: [coverage](docs/reference/coverage.md).

## License

MIT. The vendored plugins keep their own MIT licenses and authors; see [NOTICE.md](NOTICE.md).
