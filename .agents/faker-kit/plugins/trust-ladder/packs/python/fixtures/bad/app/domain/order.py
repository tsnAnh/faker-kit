from typing import Any

from app.ui.format import money


def total(lines):
    # subtotal = sum(line["price"] for line in lines)
    try:
        return money(sum(line["price"] * line["qty"] for line in lines))
    except Exception:
        print("could not total")
        return None


def parse(payload: Any) -> dict:  # type: ignore
    return payload
