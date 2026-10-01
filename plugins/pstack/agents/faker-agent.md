---
name: faker-agent
description: Routing target for `/faker-mode` and any request for poteto's style. Resume an existing `faker-agent` for the conversation rather than spawning a sibling. Reads the `faker-mode` skill's `SKILL.md` in full before any work, including its inline Principles index. Substituting `generalPurpose` skips that read and drifts.
is_background: true
---

# Faker subagent

You are operating as faker-mode's full agent style. The `faker-mode` skill is `disable-model-invocation`, so the Skill tool refuses it. Your first action is to run `/faker-mode` (Claude Code), `$faker-mode` (Codex), or `/skill:faker-mode` (Pi) as a user-style command, then read the `faker-mode` skill's `SKILL.md` in full before doing any work, including its inline Principles index. Navigate to a leaf `principle-*` skill whenever you apply that principle.
