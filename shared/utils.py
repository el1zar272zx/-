# shared/utils.py
from __future__ import annotations

import html
import re
from datetime import date, datetime, time
from functools import lru_cache
from typing import Iterable, Iterator, Sequence, TypeVar
from zoneinfo import ZoneInfo

from .config import settings

T = TypeVar("T")

WEEKDAYS_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")
WEEKDAYS_FULL = (
    "Понеділок",
    "Вівторок",
    "Середа",
    "Четвер",
    "Пʼятниця",
    "Субота",
    "Неділя",
)


@lru_cache(maxsize=1)
def get_tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def now_local() -> datetime:
    """Поточний місцевий час (за TIMEZONE) без tzinfo — як зберігаємо дати/час у БД."""
    return datetime.now(get_tz()).replace(tzinfo=None)


def today_local() -> date:
    return now_local().date()


def esc(value: object) -> str:
    """Екранування для HTML parse mode."""
    return html.escape("" if value is None else str(value))


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def fmt_date_short(d: date) -> str:
    return f"{d.strftime('%d.%m')} {WEEKDAYS_SHORT[d.weekday()]}"


def fmt_date_long(d: date) -> str:
    return f"{d.strftime('%d.%m.%Y')} ({WEEKDAYS_FULL[d.weekday()]})"


def fmt_time(t: time) -> str:
    return t.strftime("%H:%M")


def fmt_hours(start: time, end: time) -> str:
    return f"{fmt_time(start)}–{fmt_time(end)}"


def fmt_price(price: int | None) -> str:
    return f"{price} грн" if price is not None else "ціна уточнюється"


_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")
_RANGE_RE = re.compile(
    r"^\s*(\d{1,2}[:.]\d{2})\s*[-–—]\s*(\d{1,2}[:.]\d{2})(?:\s+(.*))?$", re.S
)


def parse_time(text: str) -> time | None:
    m = _TIME_RE.match(text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return time(h, mi)


def parse_time_range(text: str) -> tuple[time, time, str] | None:
    """
    "10:00-20:00" або "14:00-16:00 обід" -> (start, end, "обід").
    Повертає None, якщо формат невірний або кінець не пізніше початку.
    """
    m = _RANGE_RE.match(text or "")
    if not m:
        return None
    start, end = parse_time(m.group(1)), parse_time(m.group(2))
    if start is None or end is None or start >= end:
        return None
    return start, end, (m.group(3) or "").strip()


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if not 9 <= len(digits) <= 15:
        return None
    return ("+" if (raw or "").strip().startswith("+") else "") + digits


def parse_cb_int(data: str | None, prefix: str) -> int | None:
    """'abk_ok_15' з префіксом 'abk_ok_' -> 15. Некоректне -> None."""
    if not data or not data.startswith(prefix):
        return None
    try:
        return int(data[len(prefix):])
    except ValueError:
        return None


def chunked(items: Sequence[T], size: int) -> Iterator[list[T]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def minutes_to_time(m: int) -> time:
    return time(m // 60, m % 60)


def join_nonempty(parts: Iterable[str | None], sep: str = "\n") -> str:
    return sep.join(p for p in parts if p)
