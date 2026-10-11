---
name: run-model
description: Run one panel seat, judge, or delegate on a specific model slug (claude-opus-5-5-max, gpt-6-sol-high) from Claude Code, Codex, or Pi. Every pstack default is inherit-parent, so this is only needed when someone names a model, in ~/.agents/pstack-models.md or with /advisor <model>, that your agent cannot select directly, such as a gpt-* seat from Claude Code.
---

# Run a model seat

Every role and panel seat in the kit defaults to `inherit-parent`: it runs on your own agent, and a panel gets its independent opinions from separate runs. Each teammate uses one agent, so that is the fast path. This skill is for the exception, when a configured seat names a model. Your agent runs its own family directly. For the other family, this skill finds a route or tells you to fall back.

GPT seats always run on `gpt-6-sol` at high effort in the standard service tier. The script drops a `-fast` suffix and lowers `max` or `xhigh` to `high`, because the fast tier costs 1.5x quota.

## Steps

1. Write the seat's full prompt to a file. Include every path and excerpt the seat needs, because an external run starts with no context.
2. Run the script from this skill's directory. Pass the agent you are running in and the slug:

   ```bash
   scripts/run-model.sh --from claude --model gpt-6-sol-high < seat-prompt.md
   ```

   Use `--from codex` in Codex and `--from pi` in Pi. Add `--dry-run` to see the route without running. Add `--write` only when the seat must edit files; the default run is read-only.
3. Read the first line of output. It is JSON with `route`, `how`, and `command`.
   - `native`: do what `how` says with your own subagent tool, then continue.
   - `external`: the lines after the JSON are the seat's answer. Treat it like any subagent report: verify it before you act on it.
   - `fallback` (exit code 3): run the seat on your own model, keep the panel size, and say in your reply that the panel was single-family.

## Rules

- Never skip a seat because its model is unreachable. The fallback keeps the panel's independent samples.
- One seat per run. Launch several runs in parallel when a panel has several external seats.
- Say which route each seat took when you report a panel result.
