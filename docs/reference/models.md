# Models

The vendored plugins are byte-for-byte upstream except for model names. This page summarizes how [`upstream/model-map.json`](../../upstream/model-map.json) rewrites them. [`upstream/CHANGES.md`](../../upstream/CHANGES.md) lists every occurrence by file and line. To set your own agent's model, see [Choose models](../how-to/models.md).

## The mapping

| Upstream name | Where | Becomes |
|---|---|---|
| `grok-4.7-xhigh-fast` as a single role (code delegates, explorers, investigators, swarm workers) | pstack | `inherit-parent` |
| `claude-opus-5-5-max` as a single role (`/how` explainer, `/why` synthesizer, reflect judgment, prose, hardest tasks) | pstack | `inherit-parent` |
| The panel `claude-opus-5-5-max, gpt-5.6-sol-max, grok-4.7-xhigh-fast` | pstack arena, architect, interrogate, setup-pstack | `claude-opus-5-5-max, gpt-6-sol-max, inherit-parent` |
| `gpt-5.6-sol-max` elsewhere | pstack reflect tooling | `gpt-6-sol-max` |
| The budget example `grok-4.7-xhigh-fast` into `grok-4.7-medium-fast` | pstack | `gpt-6-sol-max` into `gpt-6-sol-medium` |
| "Families go by prefix: `claude-*`, `gpt-*`, and `grok-*`" | pstack | "`claude-*` and `gpt-*`" |
| `model: fast` in agent frontmatter | cursor-team-kit's ci-watcher, agent-compatibility's four reviewers | `model: opus` |
| Grok 4.6 as the default advisor, `grok-4.6[effort=xhigh]` | advisor | GPT-6 Sol at max effort, `gpt-6-sol-max`; the subagent's `model:` becomes `opus` |
| `composer-2*`, `gpt-5.5*`, `gpt-5.3-codex*` | orchestrate, cursor-sdk | `gpt-6-sol*`, keeping the effort suffix |
| `claude-opus-4-8*`, `claude-opus-4-7` | orchestrate | `claude-opus-5-5*` |

`inherit-parent` is upstream's own name for "omit the model and use the parent's". The rewrite leaves `inherit` and `auto` untouched.

## Allowed model names

After the rewrite, a vendored file may name only these models: `claude-opus-5-5` (with `-max`, `-medium`, or `-thinking-xhigh`), `gpt-6-sol` (with `-max`, `-high`, `-medium`, `-fast`, or `-high-fast`), and fragments of those names such as the family prefixes `claude-` and `gpt-`. [`scripts/sync-upstream.mjs`](../../scripts/sync-upstream.mjs) fails on anything else, apart from the map's `tokenExceptions`: code identifiers in a reflect example, the Grok Bot product name, and Cursor's `composer` runtime option in cursor-sdk.

## Known side effect

orchestrate's model catalog had separate entries for `gpt-5.5-high-fast` and `gpt-5.3-codex-high-fast`. Both become `gpt-6-sol-high-fast`, so one of orchestrate's own tests, which checks that catalog slugs are unique, fails (208 of 209 pass). The kit keeps the rewrite model-only instead of editing orchestrate's logic. orchestrate needs a Cursor API key, so most of the team never runs it. CI reports that test job without blocking.
