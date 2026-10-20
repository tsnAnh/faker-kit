from dataclasses import dataclass


@dataclass(frozen=True)
class Line:
    price_cents: int
    quantity: int


def total_cents(lines: list[Line]) -> int:
    return sum(line.price_cents * line.quantity for line in lines)
