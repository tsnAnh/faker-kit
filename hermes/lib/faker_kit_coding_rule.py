from __future__ import annotations

SESSION_CODING_RULE = (
    "Reuse before build: search for a maintained library, SDK or asset first and use it; if you build it, say why in the pull request."
)
SESSION_CODING_FOOTER = f"\n{SESSION_CODING_RULE}\n"

BOT_CODING_RULE = (
    "Coding rule: reuse before build, and confirm before coding.\n"
    f"{SESSION_CODING_RULE}\n"
    "Confirm before coding. Bots and the chief: before launching or briefing a coding unit, ask the owner ONE batched question "
    "(what it should do, how it looks, platforms, who may use it, failure and edge behaviour, limits, anything left open), each item with a recommended answer. "
    "Never decide a product, design or economics choice for the owner or start on an assumption you could have asked. "
    "Only a pure bug fix with obvious expected behaviour skips it. Put the answers in the brief's OWNER DECISIONS line."
)
