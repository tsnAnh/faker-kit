<!-- orchestrate failure handoff
task: {{taskName}}
branch: {{branch}}
agentId: {{agentId}}
runId: {{runId}}
failureMode: {{failureMode}}
terminatedAt: {{terminatedAt}}
-->

# {{taskName}} failure handoff

Status: error (the agent stopped without writing a handoff)
Failure mode: {{failureMode}}
Agent: {{agentId}}
Agent page: {{agentView}}
Started: {{startedAt}}
Terminated: {{terminatedAt}}
Duration: {{duration}}
Last activity: {{lastActivityLine}}
Last tool call: {{lastToolCall}}
Branch: {{branch}}
Error: {{sdkError}}

## Suggested next steps
{{suggestions}}
