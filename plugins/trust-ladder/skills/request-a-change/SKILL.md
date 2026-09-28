---
name: request-a-change
description: Let anyone on the team, including designers, marketers, and founders, turn a plain-English request or bug report into a reviewed pull request. Restates the request, builds it with poteto-mode, proves it with screenshots or video from the verification skill, updates every language, and opens a PR for an engineer to review. Never merges. Use for /request-a-change, "can you change X in the app", "the button should say Y", or a bug report from someone who is not an engineer.
---

# Request a change

The person asking may not read code. Your job is to turn their words into a change an engineer can review in minutes, with proof they can see. Keep every message to them in plain language.

## 1. Restate and confirm

Write back, in plain words:

- What will be different for users, and where in the app.
- What will stay the same.
- How you will show it works, for example "a screenshot of the checkout screen with the new wording in English and Vietnamese".

Ask one question: "Is that right?" Offer to adjust. For a bug report, restate what goes wrong, where, and what should happen instead. Do not start until they confirm, unless they already said to go ahead.

## 2. Check the ground

- Find the repository's verification skill (`verify-*`). Without one, say that you can only prove the change with tests, and suggest an engineer runs `/create-verification-skill`.
- Read AGENTS.md for the check command, paved paths, and anything this change must not touch.
- If the request touches money, permissions, personal data, or deleting things, say so and ask an engineer to take it instead. Stop there.

## 3. Build it on a branch

1. Create a branch named `request/<short-name>`. Never commit to the default branch.
2. Prefer a cloud session so the person needs no local setup (`setup-cloud-agents`). If the app cannot run in the cloud, say where it will be verified.
3. Run `/poteto-mode` with the playbook that fits: Feature for new behavior, Bug fix for a defect, Visual parity for "make it look like this design". Pass along the restated request as the task.
4. Every user-facing text change updates every supported language with a real translation.

## 4. Prove it

1. Take before and after screenshots, or a short recording, with the verification skill.
2. Run `scripts/check.sh` (or the check command in AGENTS.md). It must pass.
3. If a review bot or the check flags something, fix it and repeat the proof.

## 5. Open the pull request

Title it in plain words, for example "Checkout button says 'Place order' in every language". The body has these sections:

- **What changes for users**: two or three plain sentences.
- **Before and after**: the screenshots or recording.
- **How it was checked**: the verification steps and the check result.
- **Decisions needed**: anything the requester or reviewer must decide, or "None".
- **Technical notes**: for the reviewing engineer; files touched and anything unusual.

Request review from the code owners. Add the label `requested-change` if the repository has it.

## 6. Never merge

Do not merge, approve, or push to the default branch, even when checks are green. An engineer reviews and merges.

## Tell the requester

In plain words: what changed, the screenshots, the pull request link, who will review it, and that they will get a comment if a decision is needed.
