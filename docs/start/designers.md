# Start here: designers

You can change the product yourself: try layouts, fix spacing, match a mockup. You describe what you want in plain words. The agent builds it on a separate branch, shows you screenshots, and opens a pull request for an engineer to review. Nothing goes live without that review, so you cannot break anything by trying.

## Before you start

- Install the kit once in your agent: [Install the kit](../how-to/install.md). If you have never used a coding agent, ask an engineer to sit with you for ten minutes.
- Check that the repository has a verification skill (ask an engineer, or type `/start-here`, which checks for you). The verification skill is what lets the agent open the app and take screenshots. Without it, you will only get test results, not pictures.

The examples below are for Claude Code. In Codex, start with `$` instead of `/`; in Pi, with `/skill:`.

## Try two or three versions and pick one

```text
/poteto-mode try three layouts for the empty cart screen. show me a screenshot of each side by side.
```

The agent builds each version behind a switch, takes screenshots, and puts them next to each other. Pick one, or ask for a mix: "the second layout with the first one's illustration". The versions are throwaway. When you pick, the agent builds the chosen one properly.

## Make a screen match a design

```text
/poteto-mode make the settings screen match this mockup exactly. <paste the image or a link>
```

This uses the Visual parity playbook: the agent compares screenshots of the app with your mockup until they match, and shows you both.

## Ship a change

```text
/request-a-change the primary buttons should use 12px corner radius everywhere
```

The agent restates your request and asks "Is that right?", builds it on a branch, takes before and after screenshots, and opens a pull request that says in plain words what changes for users. An engineer reviews and merges it. You get the link.

## If something looks wrong in the app

```text
/request-a-change the checkout total overlaps the pay button on small phones
```

Describe what you see and where. The agent reproduces it, fixes it, and shows you the screenshot proof.

## Write it down so it sticks

When you find yourself saying the same design feedback in reviews ("use the spacing tokens, not raw numbers"), add it to STYLE.md, or ask an engineer to run `/correct` on it. The goal is for the rule to become a check that catches it automatically. See [the trust ladder](../explanation/trust-ladder.md).

## Also useful

- `/how is the theme applied to every screen?`: an explanation of how something works today.
- `/teach`: the agent explains a change it made, in terms you understand.
- `/bro`: if an answer is too technical, ask again in plain words.
- `/create-learning-path I want to read pull requests with confidence`

More prompts: [prompts by role](../reference/prompts.md). Why this is safe: [Why non-engineers can ship safely](../explanation/why-non-engineers-can-ship-safely.md).
