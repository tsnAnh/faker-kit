# Notice

This repository contains two kinds of material.

## Vendored upstream plugins

Everything under these directories is copied from an MIT-licensed upstream plugin repository, at the address and commit recorded in [`upstream/source.json`](upstream/source.json) and [`upstream/lock.json`](upstream/lock.json). Each directory keeps its original `LICENSE` file, with the copyright notice and permission notice the MIT license requires.

| Directory | Upstream author | License |
|---|---|---|
| `plugins/pstack/` | Lauren Tan ([@poteto](https://x.com/poteto)) | MIT, see its `LICENSE` |
| `plugins/team-workflows/` | Eric Zakariasson | MIT, see its `LICENSE` |
| `plugins/thermos/` | The upstream plugin team | MIT, see its `LICENSE` |
| `plugins/continual-learning/` | Eric Zakariasson | MIT, see its `LICENSE` |
| `plugins/cli-for-agent/` | Eric Zakariasson | MIT, see its `LICENSE` |
| `plugins/agent-compatibility/` | The upstream plugin team | MIT, see its `LICENSE` |
| `plugins/advisor/` | The upstream plugin team | MIT, see its `LICENSE` |
| `plugins/teaching/` | The upstream plugin team | MIT, see its `LICENSE` |

The copies are byte-identical to upstream with five exceptions, all applied by [`scripts/sync-upstream.mjs`](scripts/sync-upstream.mjs) and listed occurrence by occurrence in [`upstream/CHANGES.md`](upstream/CHANGES.md):

1. **Model names.** [`upstream/model-map.json`](upstream/model-map.json) rewrites LLM model names, and the sentences that describe them, to the models this team uses.
2. **Three frontmatter load fixes.** [`upstream/compat-patches.json`](upstream/compat-patches.json) makes two skill names invocable so Pi and Codex can call them, and quotes the `check-agent-compatibility` description so Pi can parse it.
3. **Team renames.** [`upstream/renames.json`](upstream/renames.json) renames pstack's `poteto-mode` skill to `faker-mode`, its `poteto-agent` to `faker-agent`, and one plugin to `team-workflows`, in file names and text. The credit to poteto (Lauren Tan) for the style stays.
4. **No editor name.** The same file keeps the upstream editor's name out of the kit: its project folders become `.agents/`, its environment variables become the ones Claude Code and Codex set, and sentences that named it are reworded. The `LICENSE` files are not changed. The sync fails if the name comes back anywhere else.
5. **Files added and left out.** Each plugin gains a generated `.claude-plugin/plugin.json`, so Claude Code and Codex can install it, and plugins with upstream hooks gain `agent-hooks/`, which runs those hooks as Claude Code and Codex hooks. The upstream editor's own plugin manifests are not copied.

`node scripts/sync-upstream.mjs --check` fails CI if any other byte differs.

## Ported material

`plugins/trust-ladder/skills/orchestrate/` is ported from the MIT-licensed `orchestrate` plugin in the same upstream repository, at commit `4e5b1cf2ccb0`, and keeps its `LICENSE` file with the original copyright and permission notices. The port replaces the upstream editor's agent SDK with `scripts/runtime/`, which drives Claude Code and Codex through their own command-line tools, in the cloud or as local processes; adapts the prompts, references, model catalog, and tests to those agents; and removes the editor's name. The planning loop, handoff format, verifiers, measurements, Andon, and Slack mirroring are the upstream design.

## Original material

Everything else, including the rest of `plugins/trust-ladder/`, `docs/`, `scripts/`, and the templates and lint packs, is Copyright (c) 2026 tsnAnh under the MIT license in [`LICENSE`](LICENSE).

`docs/` summarizes ideas from Lauren Tan's talk on building trust in agents and her "Complete Guide to pstack" posts, and quotes some of their example prompts. The generated pages in `docs/reference/` quote each vendored skill's description and poteto-mode's playbook list. [poteto/verification-skill-example](https://github.com/poteto/verification-skill-example) is referenced as an example and nothing is copied from it.
