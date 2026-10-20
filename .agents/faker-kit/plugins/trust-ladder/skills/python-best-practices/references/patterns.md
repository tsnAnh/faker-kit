# Python patterns

Examples for each rule in `SKILL.md`. The snippet passes `ruff check` and `mypy --strict` with the python lint pack's configuration.

```python
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import NewType, assert_never

logger = logging.getLogger(__name__)

OrderId = NewType("OrderId", str)
_ORDER_ID = re.compile(r"^ord_[a-z0-9]{12}$")


class BadOrderIdError(ValueError):
    def __init__(self, raw: str) -> None:
        super().__init__(f"{raw!r} is not an order id")


def parse_order_id(raw: str) -> OrderId:
    if not _ORDER_ID.match(raw):
        raise BadOrderIdError(raw)
    return OrderId(raw)


class Failure(Enum):
    NETWORK = "network"
    SIGNED_OUT = "signed_out"


@dataclass(frozen=True, slots=True)
class Loading:
    pass


@dataclass(frozen=True, slots=True)
class Ready:
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Failed:
    reason: Failure


CartState = Loading | Ready | Failed


def title(state: CartState) -> str:
    match state:
        case Loading():
            return "Loading"
        case Ready(items):
            return f"Cart ({len(items)})"
        case Failed(reason):
            logger.info("cart unavailable", extra={"reason": reason.value})
            return "Cart"
        case _:
            assert_never(state)


def parse_cart(payload: object) -> Ready:
    match payload:
        case {"items": list(items)} if all(isinstance(item, str) for item in items):
            return Ready(tuple(str(item) for item in items))
        case _:
            raise ValueError("cart payload must be {'items': [str, ...]}")
```
