<!-- team-kit:verdict v1 -->
verification: <one of: live-ui-verified | unit-test-verified | type-check-only | verifier-blocked | verifier-failed>
pr: <pull request URL>
head: <the commit SHA you checked out and verified>
sig: <HMAC-SHA256, in hex, of "<verification>|<head>" under the key in your prompt>

## Execution
- `<command run>`: <outcome>
- <manual or driven step>: <observed behavior>

## Findings
Per Acceptance line:
- [x] <line>: <evidence> (met | not met | n/a)
Other findings, most severe first:
- (high) <finding>: <evidence>

## Notes
- <flaky tests, environment trouble, follow-ups, or "none">
