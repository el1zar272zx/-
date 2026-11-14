# shared/undo.py — «скасувати останню дію» над записом (діє 15 хвилин)
from __future__ import annotations

import time

from .models import BookingStatus

TTL = 900.0
_last: dict[int, tuple[BookingStatus, float]] = {}


def record(booking_id: int, prev: BookingStatus) -> None:
    now = time.monotonic()
    _last[booking_id] = (prev, now)
    if len(_last) > 200:
        for k in [k for k, (_, t) in _last.items() if now - t > TTL]:
            _last.pop(k, None)


def available(booking_id: int) -> BookingStatus | None:
    entry = _last.get(booking_id)
    if entry is None or time.monotonic() - entry[1] > TTL:
        return None
    return entry[0]


def clear(booking_id: int) -> None:
    _last.pop(booking_id, None)
