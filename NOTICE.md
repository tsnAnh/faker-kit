# Notice

This repository contains two kinds of material.

## Vendored upstream plugins

Everything under these directories is copied from [cursor/plugins](https://github.com/cursor/plugins) at the commit recorded in [`upstream/lock.json`](upstream/lock.json). Each directory keeps its original `LICENSE` file.

| Directory | Upstream author | License |
|---|---|---|
| `plugins/pstack/` | Lauren Tan ([@poteto](https://x.com/poteto)) | MIT, Copyright (c) 2026 Lauren Tan |
| `plugins/cursor-team-kit/` | Eric Zakariasson, Cursor | MIT, Copyright (c) 2026 Cursor |
| `plugins/thermos/` | Cursor | MIT, Copyright (c) 2026 Cursor |
| `plugins/continual-learning/` | Eric Zakariasson, Cursor | MIT, Copyright (c) 2026 Cursor |
| `plugins/cli-for-agent/` | Eric Zakariasson, Cursor | MIT, Copyright (c) 2026 Cursor |
| `plugins/agent-compatibility/` | Cursor | MIT, Copyright (c) 2026 Cursor |
| `plugins/advisor/` | Cursor | MIT, Copyright (c) 2026 Cursor |
| `plugins/teaching/` | Cursor | MIT, Copyright (c) 2026 Cursor |

The copies are byte-identical to upstream with four exceptions, all applied by [`scripts/sync-upstream.mjs`](scripts/sync-upstream.mjs) and listed occurrence by occurrence in [`upstream/CHANGES.md`](upstream/CHANGES.md):

1. **Model names.** [`upstream/model-map.json`](upstream/model-map.json) rewrites LLM model names, and the sentences that describe them, to the models this team uses.
2. **Three frontmatter load fixes.** [`upstream/compat-patches.json`](upstream/compat-patches.json) makes `Poteto Mode` and `Make Bot UI` invocable names so Pi and Codex can call them, and quotes the `check-agent-compatibility` description so Pi can parse it.
3. **Team renames.** [`upstream/renames.json`](upstream/renames.json) renames pstack's `poteto-mode` skill to `faker-mode` and its `poteto-agent` to `faker-agent`, in file names and text. The credit to poteto (Lauren Tan) for the style stays.
4. **Generated files.** Each plugin gains a `.claude-plugin/plugin.json` built from its `.cursor-plugin/plugin.json`, so Claude Code and Codex can install it. Plugins with Cursor hooks also gain `agent-hooks/`, which runs those hooks, unchanged, as Claude Code and Codex hooks. The Cursor files stay in place.

`node scripts/sync-upstream.mjs --check` fails CI if any other byte differs.

## Original material

Everything else, including `plugins/trust-ladder/`, `docs/`, `scripts/`, and the templates and lint packs, is Copyright (c) 2026 tsnAnh under the MIT license in [`LICENSE`](LICENSE).

`docs/` summarizes ideas from Lauren Tan's talk on building trust in agents and her "Complete Guide to pstack" posts, and quotes some of their example prompts. The generated pages in `docs/reference/` quote each vendored skill's description and poteto-mode's playbook list. [poteto/verification-skill-example](https://github.com/poteto/verification-skill-example) is referenced as an example and nothing is copied from it.
