<!-- team-kit:diagnosis v1 -->
suspect: <the full commit you believe caused it, or none>
confidence: <high | medium | low>
sig: <HMAC-SHA256, in hex, of "<suspect or none>|<confidence>|<issue number>" under the key in your prompt>

## What is failing
- <the failing check, test, or endpoint, and its error, quoted briefly>

## Evidence
- `<command run>`: <what it showed>
- <for a suspect: the line in its diff that explains the failure>

## Smallest safe fix
- <revert <commit>, or the change that fixes it, in one or two lines; or "unknown">

## Not checked
- <what you could not reach or run, or "nothing">
