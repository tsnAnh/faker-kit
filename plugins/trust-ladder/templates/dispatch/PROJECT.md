# <Project name>

<Two sentences: what the project is for and who uses it.>

Project bot: `<bot>` on the owner's Hermes machine. It plans units across the repositories below and launches a cloud coding agent per unit. The owner merges.

## Repositories

| Repository | What it is | Its contract |
|---|---|---|
| `<OWNER/REPO-api>` | <The backend.> | <`openapi.yaml`, a schema, or "none"> |
| `<OWNER/REPO-web>` | <The web app.> | <Consumes the API's contract.> |
| `<OWNER/REPO-mobile>` | <The mobile app.> | <Consumes the API's contract.> |

## How they talk

<Who calls whom, over what: "web and mobile call the API over HTTPS; the API sends push notifications through ...".>

## Decisions

Each decision that more than one repository depends on is one file in `docs/decisions/`, named `NNNN-title.md`. A contract unit writes it before any repository implements it. The provider implements it next, then the consumers, each in its own unit.
