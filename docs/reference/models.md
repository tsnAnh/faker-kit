# Models

The vendored plugins are byte-for-byte upstream except for model names, the sentences that describe them, and the renames in [`upstream/renames.json`](../../upstream/renames.json). This page summarizes how [`upstream/model-map.json`](../../upstream/model-map.json) rewrites the models. [`upstream/CHANGES.md`](../../upstream/CHANGES.md) lists every occurrence by file and line. To set your own agent's model, see [Choose the model](../how-to/install.md#choose-the-model).

## The rule

Each teammate runs Claude Code (Claude Opus 5.5, max effort). Claude Code's agent tool accepts the aliases `sonnet`, `opus`, and `haiku`, not version slugs. So code delegates run on `sonnet` (Claude Sonnet 5.5) and trivial mechanical edits on `haiku` (Claude Haiku 5.5). Every other role and every panel seat becomes `inherit-parent`, upstream's own name for "omit the model and use the parent's". A model name survives only as an example a person can type, or as a cross-family judge default in `/arena`. A Claude name there is `claude-opus-5-5-max` and a GPT name is `gpt-6-sol-high`.

## The mapping

| Upstream | Where | Becomes |
|---|---|---|
| `grok-4.7-xhigh-fast` as the code default (feature, refactoring, bug-fix, perf-issue, hillclimb) | pstack's `faker-mode` skill, its five code playbooks, `setup-pstack`, the pstack README | `sonnet`, with `haiku` for a trivial mechanical edit (the `trivial edits` line) |
| `claude-opus-5-5-xhigh` for the hardest changes | pstack's `faker-mode` skill, `setup-pstack` | `sonnet` |
| The panel `claude-opus-5-5-xhigh, grok-4.7-xhigh-fast` | `/architect` and `/arena` runners, `setup-pstack` role lines | `inherit-parent` for each seat |
| `/arena`'s cross-judge pool defaults, where the judge is never from the parent's family | pstack `arena` | `claude-opus-5-5-max` and `gpt-6-sol-high` |
| "Families go by prefix", which listed the `claude-*`, Grok, and Composer prefixes | pstack `arena` | "`claude-*` and `gpt-*`" |
| Any other `grok-4.7-xhigh-fast` role (explorers, investigators, swarm workers) | pstack | `inherit-parent` |
| Any other `claude-opus-5-5-xhigh` role (`/how` explainer, `/why` synthesizer, reflect judgment, prose and judgment) | pstack | `inherit-parent` |
| The budget example for `unlimited`, `large`, and `small` | pstack `setup-pstack` | `gpt-6-sol-high` into `gpt-6-sol-medium` for `small` |
| `model: fast` in agent frontmatter | team-workflows's ci-watcher, agent-compatibility's four reviewers | `model: inherit` |
| The default advisor, Grok 4.6 at xhigh effort | advisor | `inherit-parent`; the advisor agent's `model:` becomes `inherit` |
| The advisor's example slugs and family names | advisor | `gpt-6-sol-high` and `claude-opus-5-5-max`; `gpt sol` and `claude opus` |

`/interrogate` no longer has model defaults of its own. It reads the `interrogate reviewers` line and borrows the runner rules from `/arena`.

## Allowed model names

After the rewrite, a vendored file may name only `claude-opus-5-5` (with `-max` or `-medium`), `gpt-6-sol` (with `-high` or `-medium`), and the family prefixes `claude-` and `gpt-`. [`scripts/sync-upstream.mjs`](../../scripts/sync-upstream.mjs) fails on anything else, apart from the map's `tokenExceptions`: code identifiers in a reflect example, and the example slugs in pstack's Claude Code adapter.
