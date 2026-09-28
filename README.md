# team-kit

A team kit for Claude Code, Codex, and Pi that makes coding agents trustworthy enough to run many at once, and safe enough for anyone on the team to use.

It packages the Cursor team's agent plugins (Lauren Tan's pstack, cursor-team-kit, thermos, and five more) word for word, with only model names changed to ours, and adds `trust-ladder`: skills that turn every correction you give an agent into a lasting fix, lint packs for seven stacks, review bots, routines, and plain-language skills for designers, marketers, and founders.

## Install

Pick the one agent you use.

**Claude Code**

```text
/plugin marketplace add tsnAnh/skills
/plugin install trust-ladder@team-kit
```

**Codex**

```bash
codex plugin marketplace add tsnAnh/skills
for p in trust-ladder pstack cursor-team-kit thermos continual-learning cli-for-agent agent-compatibility advisor teaching; do codex plugin add "$p@team-kit"; done
```

**Pi**

```bash
pi install git:github.com/tsnAnh/skills
```

Then type `/start-here` (Codex: `$start-here`, Pi: `/skill:start-here`). Details, team-wide setup, and updating: [Install the kit](docs/how-to/install.md).

## Start with your role

| You are | Read |
|---|---|
| An engineer | [Start here: engineers](docs/start/engineers.md) |
| A designer | [Start here: designers](docs/start/designers.md) |
| In product or marketing | [Start here: product and marketing](docs/start/product-and-marketing.md) |
| A founder or lead | [Start here: founders and leads](docs/start/founders-and-leads.md) |

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
| `plugins/pstack/` and seven others | Vendored from [cursor/plugins](https://github.com/cursor/plugins), byte-identical except model names and the `faker-mode` rename, plus generated manifests and hook files ([what changed](upstream/CHANGES.md)) |
| `docs/` | Usage instructions for every role |
| `upstream/` | The pinned upstream commit, the model map, the team's renames, the Cursor hook adapter, and the change list |
| `scripts/` | Sync, validation, catalog generation, and the test runners CI uses |

Every skill, with who it is for and a prompt to try: [docs/reference/skills.md](docs/reference/skills.md). Where everything from the talk and the pstack guide posts lives: [coverage](docs/reference/coverage.md).

## License

MIT. The vendored plugins keep their own MIT licenses and authors; see [NOTICE.md](NOTICE.md).
