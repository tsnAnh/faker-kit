from __future__ import annotations

CI_RULE = (
    "CI rule: do not wait for CI. After your last push, open the pull request (ready when your own work is done, a draft only when you could not finish), "
    "post your closing summary and end the session. Never sleep, poll or loop on `gh run`, `gh pr checks` or the check-runs API: "
    "the kit and the project bot watch CI and send a follow-up if a check goes red. "
    "You may run the project's own tests and checks locally to prove the change, and read the check results once, without waiting, "
    "to report them in the pull request body; never block on a check that is still pending."
)
CI_FOOTER = f"\n{CI_RULE}\n"
