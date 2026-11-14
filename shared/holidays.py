# shared/holidays.py — державні свята України: бот лише ПРОПОНУЄ закрити день, рішення за майстром
from __future__ import annotations

from datetime import date, timedelta

from .care import get_flag
from .database import session_scope
from .models import SalonInfo

FIXED = {
    (1, 1): "Новий рік",
    (3, 8): "8 Березня",
    (6, 28): "День Конституції",
    (8, 24): "День Незалежності",
    (10, 1): "День захисників і захисниць",
    (12, 25): "Різдво",
}


def orthodox_easter(year: int) -> date:
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    return date(year, month, day + 1) + timedelta(days=13)


def holidays_between(first: date, last: date) -> list[tuple[date, str]]:
    out: list[tuple[date, str]] = []
    for year in {first.year, last.year}:
        for (m, d), name in FIXED.items():
            out.append((date(year, m, d), name))
        easter = orthodox_easter(year)
        out.append((easter, "Великдень"))
        out.append((easter + timedelta(days=1), "Великодній понеділок"))
    return sorted((d, n) for d, n in out if first <= d <= last)


async def decision(day: date) -> str | None:
    async with session_scope() as session:
        row = await session.get(SalonInfo, f"hol_{day.isoformat()}")
    return row.value if row else None


async def set_decision(day: date, value: str) -> None:
    async with session_scope() as session:
        key = f"hol_{day.isoformat()}"
        row = await session.get(SalonInfo, key)
        if row is None:
            session.add(SalonInfo(key=key, value=value))
        else:
            row.value = value


async def enabled() -> bool:
    return await get_flag("holidays_on")
