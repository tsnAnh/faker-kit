# Models

The vendored plugins are byte-for-byte upstream except for model names, the sentences that describe them, and the renames in [`upstream/renames.json`](../../upstream/renames.json). This page summarizes how [`upstream/model-map.json`](../../upstream/model-map.json) rewrites the models. [`upstream/CHANGES.md`](../../upstream/CHANGES.md) lists every occurrence by file and line. To set your own agent's model, see [Choose models](../how-to/models.md).

## The rule

Each teammate has one agent: Claude Code (Claude Opus 5.5, max effort) or Codex (GPT-6 Sol, high effort, never the fast tier). A seat that names the other family would always fall back, and one that names your own family is the model you already run. So every role and every panel seat becomes `inherit-parent`, upstream's own name for "omit the model and use the parent's". A model name survives only as an example a person can type, and a GPT name is always `gpt-6-sol-high`.

## The mapping

| Upstream | Where | Becomes |
|---|---|---|
| The panel `claude-opus-5-5-max, gpt-5.6-sol-max, grok-4.7-xhigh-fast` | pstack arena runners and cross-judge pool, architect runners, interrogate reviewers, setup-pstack | `inherit-parent, inherit-parent, inherit-parent` |
| Interrogate's Reviewer A, B, and C defaults | pstack | `inherit-parent` each |
| Arena's "with no family match, use `claude-opus-5-5-max`" | pstack | `inherit-parent` |
| `grok-4.7-xhigh-fast` as a single role (code delegates, explorers, investigators, swarm workers) | pstack | `inherit-parent` |
| `claude-opus-5-5-max` as a single role (`/how` explainer, `/why` synthesizer, reflect judgment, prose, hardest tasks) | pstack | `inherit-parent` |
| `gpt-5.6-sol-max` for reflect tooling | pstack | `inherit-parent` |
| The budget example `grok-4.7-xhigh-fast` into `grok-4.7-medium-fast` | pstack setup-pstack | `gpt-6-sol-high` into `gpt-6-sol-medium` |
| "Families go by prefix: `claude-*`, `gpt-*`, and `grok-*`" | pstack | "`claude-*` and `gpt-*`" |
| `model: fast` in agent frontmatter | team-workflows's ci-watcher, agent-compatibility's four reviewers | `model: inherit` |
| The default advisor, Grok 4.6 at xhigh effort | advisor | `inherit-parent`; the advisor agent's `model:` becomes `inherit` |
| The advisor's example slugs and family names | advisor | `gpt-6-sol-high` and `claude-opus-5-5-max`; `gpt sol` and `claude opus` |

## Allowed model names

After the rewrite, a vendored file may name only `claude-opus-5-5` (with `-max` or `-medium`), `gpt-6-sol` (with `-high` or `-medium`), and the family prefixes `claude-` and `gpt-`. [`scripts/sync-upstream.mjs`](../../scripts/sync-upstream.mjs) fails on anything else, apart from the map's `tokenExceptions`: code identifiers in a reflect example.

## Outside the vendored plugins

| Where | Model |
|---|---|
| `templates/codex-config.toml` | `gpt-6-sol`, `model_reasoning_effort = "high"`, `service_tier = "default"` |
| Claude Code routines, including the PR review (`setup-review-bots`, `setup-routines`) | Claude Opus 5.5, chosen in the routine's model selector |
| Codex code review | Codex's own review model |
| `templates/github/codex-review.yml` (API-key alternative) | `gpt-6-sol`, effort `high`, `service_tier="default"` |
| `templates/github/claude-review.yml` and the routine workflows (API-key alternative) | `claude-opus-5-5`, effort `max` |
| `run-model` | GPT at `high` in the standard tier, whatever the slug says; Claude at the slug's effort, `max` by default |
