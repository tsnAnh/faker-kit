# GitHub payload fixtures

These are webhook payloads GitHub recorded, copied from [octokit/webhooks](https://github.com/octokit/webhooks) at commit `7dd7fa56498a827a08b71919fae89428f5e8e283`, directory `payload-examples/api.github.com/`. They are reformatted without whitespace and otherwise unchanged. Their MIT license is in `LICENSE` beside this file.

The tests copy a payload and set only the fields a unit event needs: the PR or issue body, the comment body, and `merged`. Every other field is as GitHub sent it.
