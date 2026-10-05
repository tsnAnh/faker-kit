<!-- team-kit:security v1 -->
range: <base commit from your prompt, or none>..<the full head commit from your prompt>
findings: <N> high, <N> medium, <N> low
sig: <HMAC-SHA256, in hex, of "<high>|<medium>|<low>|<head>" under the key in your prompt>

## Scope
- Commits: <count>, `<base>..<head>`. Files read: <count>.
- Not reviewed: <what you could not read or run, or "nothing">

## Findings
Most severe first. One block per finding:

### (high) <title>
- Where: `<path>:<line>` in <commit>
- What an attacker does: <the concrete path from input to impact>
- Evidence: <the code, a test you ran, or the command output>
- Fix: <the smallest change that closes it>

## Checked and clear
- <category>: <what you looked at, in one line>
