---
name: python-best-practices
description: Python best practices for services, scripts, and libraries. Use when reading or editing any .py file or pyproject.toml.
paths: ["**/*.py", "**/pyproject.toml"]
---

# Python best practices

Apply the **principle-type-system-discipline** principle skill from pstack first. This table grounds it in Python. Examples: `references/patterns.md`, checked with ruff and mypy in the python lint pack's configuration.

| Rule | Summary |
|------|---------|
| Everything typed | Annotate every function. mypy `strict` passes. `Any` is banned; use `object` and narrow. |
| Variants as types | Model states as frozen dataclasses joined in a union, or an `Enum`. No `loading: bool` next to `Optional` fields. |
| Exhaustive `match` | `match` over the union or enum with `assert_never` in the fallback, so mypy fails when a variant is added (`exhaustive-match`). |
| NewType ids | `OrderId = NewType("OrderId", str)`, created only by a parse function that validates. |
| Parse at the boundary | Turn request bodies, env vars, and rows into typed objects where they enter (pydantic, attrs, or a dataclass constructor). Inner code never sees `dict[str, object]`. |
| Immutable by default | `@dataclass(frozen=True, slots=True)`, tuples for fixed sequences. |
| Specific exceptions | Raise and catch specific exception types. No bare `except`, no `except Exception: pass`. |
| Layers | Respect the import-linter contract: lower layers never import higher ones. |
| Async | No blocking calls inside `async def`. Use `asyncio.TaskGroup` for concurrent work. |
| Imports at the top | No imports inside functions (`PLC0415`); they hide a module's real dependencies. |
| Real tests | pytest against real objects and in-memory fakes. Assert what a caller observes. |
| Logging | The `logging` module with context in `extra`. No `print`. |
