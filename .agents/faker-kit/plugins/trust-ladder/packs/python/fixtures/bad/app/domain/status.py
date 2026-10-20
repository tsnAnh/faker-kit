from enum import Enum


class Status(Enum):
    OPEN = "open"
    PAID = "paid"


def label(status: Status) -> str:
    match status:
        case Status.OPEN:
            return "Open"
    return "?"


def lazy() -> None:
    import json

    json.dumps({})
