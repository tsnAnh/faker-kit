from __future__ import annotations

CI_RULE = (
    "CI rule: never wait for CI or sleep, poll or loop on `gh run`, `gh pr checks` or the check-runs API: "
    "push, open the pull request, post the summary, end."
)
CI_FOOTER = f"\n{CI_RULE}\n"
