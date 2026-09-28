# Start here: product and marketing

You can change copy, report bugs so they get reproduced for you, and get straight answers about how the product works, without waiting for an engineer's time. Everything you change goes through a pull request an engineer reviews, so nothing goes live by accident.

Nothing to install: open claude.ai/code, chatgpt.com/codex, or either app, pick the repository, type `/start-here`, and say what you want; it picks the right skill. [Use the kit from a browser or your phone](../how-to/use-the-kit-from-the-browser.md) shows how. You can also [install the kit](../how-to/install.md) on your own computer. The examples below are for Claude Code. In Codex, start with `$` instead of `/`; in Pi, with `/skill:`.

## Change text in the app

```text
/request-a-change the onboarding headline should say "Plan your week in two minutes"
```

The agent restates the change, updates every language the app supports with a real translation, takes before and after screenshots, and opens a pull request. You get the link and the screenshots. An engineer reviews and merges.

No agent open? If the repository has the **Request a change** issue form, fill it in on GitHub; an agent picks it up and links the pull request on the issue. On a Codex team, you can also assign the issue to Codex, or mention `@Codex` in Slack or Linear.

For a bigger change, describe the outcome, not the implementation: "users who skip onboarding should see a one-line tip on the home screen the first time".

## Report a bug

```text
/request-a-change after changing the language to Vietnamese, the plan names still show in English
```

Say what you did, what you saw, and what you expected. The agent reproduces it in the running app, finds the cause, and opens a fix with proof. If it cannot reproduce it, it tells you what it tried.

Teams that set up routines can also have every new bug report reproduced automatically; see [Run many agents](../how-to/run-many-agents.md#routines).

## Ask how or why

```text
/how does the free trial end?
/why do we ask for a phone number at sign-up?
```

`/how` explains how something works today. `/why` digs through history (pull requests, commits, issues, and any other sources your agent is connected to) to explain why it was built that way. `/teach` combines both into one plain explanation, drawn step by step. Add `/bro` if the answer is too technical.

## Write and polish

```text
/technical-writing draft release notes for everything merged this week
can we unslop this announcement?
```

`/technical-writing` structures a document for its reader. `/unslop` removes the tells of machine-written prose. `/what-did-i-get-done` summarizes your own recent work; `/weekly-review` does the same for a week, and covers the whole team if you ask for every author.

## Learn

```text
/create-learning-path I want to understand our analytics events well enough to add one
```

More prompts: [prompts by role](../reference/prompts.md).
