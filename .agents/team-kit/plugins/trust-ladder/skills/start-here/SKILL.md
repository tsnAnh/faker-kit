---
name: start-here
description: A plain-English front door to the team kit for anyone, including designers, marketers, product people, and founders who do not know the skill names. Asks what the person wants to do, picks the right skill or playbook, and explains each step without jargon. Use for /start-here, "what can you do", "help me change something in the app", "how do I use this", or when someone new to the kit asks for help.
---

# Start here

The person may not be an engineer. Speak plainly: short sentences, no internal names unless they ask, and show results as screenshots and plain summaries rather than code. Never ask them something you can find out by looking at the repository.

## 1. Ask what they want

Ask one question with these choices, using your agent's question tool (AskUserQuestion in Claude Code, `ask_user` in Pi, or a numbered list in Codex):

1. **Change something in the app**: copy, a layout, a small feature, a setting.
2. **Something is broken**: report a bug, or fix one.
3. **Try a few design options** and pick one.
4. **Understand how something works**, or why it is built that way.
5. **See what shipped** recently.
6. **Write or polish text**: release notes, help docs, announcement copy.
7. **Learn a skill**, such as reading a pull request or writing good prompts.
8. **Set this repository up for agents** (usually an engineer).

If their first message already says which one, skip the question.

## 2. Route it

| Choice | Do this |
|---|---|
| 1. Change something | The `request-a-change` skill. |
| 2. Something is broken | Engineers: `/faker-mode` with the Bug fix playbook. Everyone else: `request-a-change`, framed as a bug. It restates the problem, reproduces it, and opens a fix for an engineer to review. |
| 3. Design options | `/faker-mode` with the Prototype playbook: two or three variants behind a switch, a screenshot of each from the verification skill, and a side-by-side comparison. For "make it match this design", the Visual parity playbook. |
| 4. How or why | `/teach`, which runs `/how` and `/why` and explains with diagrams. Follow with `/bro` if the answer is still too technical. |
| 5. What shipped | `/what-did-i-get-done` or `/weekly-review` for the person's own commits. For the whole team, run `/weekly-review` and tell it to include every author, or point them to the weekly review issue if the team runs that routine. |
| 6. Write or polish text | `/technical-writing` for documents, then `/unslop` for tone. For text inside the app, use `request-a-change` so every language gets updated. |
| 7. Learn a skill | `create-learning-path` from the teaching plugin. |
| 8. Set up the repository | `setup-team-kit`. |

Before routing to 1, 2, or 3, check whether the repository has a verification skill (`verify-*` under `.agents/skills` or `.claude/skills`). Without one, an agent cannot show proof on screen. Say so plainly, suggest that an engineer runs `/create-verification-skill`, and offer to continue with test results as the only proof.

Also check where you are running. In a cloud session (`CLAUDE_CODE_REMOTE` is `true`, or a Codex cloud task), the person needs nothing installed, so never talk about installing. In Codex cloud, also tell them that at the end they press the button to open the pull request. Read the `Cloud sessions` section of AGENTS.md: if the app cannot run here (an iOS app, or an Android emulator without KVM), tell them which proof they will get instead, such as screenshot tests, screenshots from a CI job, or a test build on their phone. On a laptop, if the person is not an engineer and the repository has a `Cloud sessions` section, mention that they can do the same from claude.ai/code or the Claude app with nothing installed.

## 3. Say what will happen

Before starting, tell the person in two or three sentences what you are about to do, what they will get at the end (usually a pull request link with screenshots), and who reviews it. Nothing they ask for goes live without an engineer's review.

## 4. Finish in plain words

End with:

- What changed or what you found, in one or two sentences.
- The evidence: screenshots, a short video, or test results. Show the images in the conversation, not only in the pull request.
- The link, if there is one.
- What happens next and whether anything waits on them.
