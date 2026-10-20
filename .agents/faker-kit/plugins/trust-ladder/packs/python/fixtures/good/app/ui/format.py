from app.domain.order import Line, total_cents


def money(lines: list[Line]) -> str:
    return f"${total_cents(lines) / 100:.2f}"
