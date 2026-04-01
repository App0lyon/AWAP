"""Cron-like scheduling helpers used by the lightweight trigger service."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def schedule_bucket(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")


def cron_matches(expression: str, value: datetime) -> bool:
    parts = expression.split()
    if len(parts) != 5:
        return False

    minute, hour, day, month, weekday = parts
    current = value.astimezone(UTC)
    return all(
        (
            _field_matches(minute, current.minute, 0, 59),
            _field_matches(hour, current.hour, 0, 23),
            _field_matches(day, current.day, 1, 31),
            _field_matches(month, current.month, 1, 12),
            _field_matches(weekday, (current.weekday() + 1) % 7, 0, 6),
        )
    )


def _field_matches(expression: str, value: int, minimum: int, maximum: int) -> bool:
    for part in expression.split(","):
        if _part_matches(part.strip(), value, minimum, maximum):
            return True
    return False


def _part_matches(expression: str, value: int, minimum: int, maximum: int) -> bool:
    if expression == "*":
        return True
    if expression.startswith("*/"):
        step = int(expression[2:])
        return step > 0 and value % step == 0
    if "-" in expression:
        start_text, end_text = expression.split("-", 1)
        start = int(start_text)
        end = int(end_text)
        return start <= value <= end
    try:
        parsed = int(expression)
    except ValueError:
        return False
    if parsed < minimum or parsed > maximum:
        return False
    return value == parsed
