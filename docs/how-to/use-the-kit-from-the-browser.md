# Use the kit from a browser or your phone

You do not need to install anything. The agent runs on its own machine in the cloud, with the repository and the kit already loaded. You type what you want; you get back screenshots and a pull request link. Nothing goes live until an engineer reviews and merges it.

This works once an engineer has set the repository up for cloud sessions with `/setup-cloud-agents`. If you are not sure, ask, or try step 2 and see whether `/start-here` answers.

## 1. What you need

- A seat on the team's Claude plan (for Claude Code on the web) or ChatGPT plan (for Codex on the web).
- Your GitHub account connected to it, with access to the repository.

## 2. Start a session

**Claude Code on the web**, at claude.ai/code, or the Code tab in the Claude desktop or mobile app:

1. Pick the repository and the team's environment (it is usually named after the repository).
2. Type `/start-here`, or say what you want in plain words: `/request-a-change the onboarding headline should say "Plan your week in two minutes"`.
3. Keep the tab open, or close it and come back later: the session keeps working on its own machine.

**Codex on the web**, at chatgpt.com/codex: pick the repository's environment and start with `$start-here`. If Codex does not know the skill, use Claude Code on the web instead, or ask an engineer to check the environment.

The examples on your role's start page work the same way here.

## 3. What you get back

- The agent restates your request and asks "Is that right?" Answer in plain words.
- Before and after screenshots, in the conversation and on the pull request. For a web app you may also get a preview link to click through.
- A pull request link. An engineer reviews it; you get a comment if a decision is needed.

Some apps cannot run on a cloud machine, such as an iPhone app. The agent says so at the start and tells you what you will get instead: screenshots from automated screen tests, screenshots posted by the team's build system, or a test build you open on your phone.

## Without opening an agent at all

If the repository has the **Request a change** issue form, open a new issue on GitHub, pick that form, and fill it in. An agent picks it up, comments its restatement, and links a pull request with screenshots on the issue. This is the easiest door for a quick copy change or a bug you just saw.

## From your phone

Founders and leads: the Claude app on your phone runs the same cloud sessions. Useful ones:

```text
/weekly-review for every author on main, not just me
/why did we move payments to a separate service?
/request-a-change the pricing page should say "Cancel anytime" under each plan
```

## If something does not work

| You see | What it means | Who fixes it |
|---|---|---|
| The agent does not know `/start-here` | The kit does not load in this cloud environment | An engineer, with `/setup-cloud-agents` |
| No screenshots, only test results | The repository has no verification skill, or this app cannot run in the cloud | An engineer, with `/create-verification-skill` or `/setup-cloud-agents` |
| The agent cannot push or open a pull request | Your GitHub connection lacks access to the repository | You reconnect GitHub, or a repository admin grants access |
| The agent says the request needs an engineer | It touches money, permissions, personal data, or deleting things | An engineer takes it from the conversation or the issue |

Prefer to run the agent on your own computer? [Install the kit](install.md).
