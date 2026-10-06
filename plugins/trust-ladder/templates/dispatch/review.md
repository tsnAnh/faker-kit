<!-- team-kit:review v1 -->
pr: <pull request URL>
head: <the full commit SHA you reviewed>
findings: <N> high, <N> medium, <N> low
sig: <HMAC-SHA256, in hex, of "<high>|<medium>|<low>|<pr>|<head>" under the key in your prompt>

## Findings
Most severe first. One block per finding:

### (high) <title>
- Severity: high
- File: `<path>:<line>`
- Evidence: <the code path, command output, or reasoning that proves this is real>
- Fix: <the smallest change that closes it>

## Checked and Clear
- <area>: <what you read or ran, in one line>
