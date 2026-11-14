# shared/emptyhours.py — які години зазвичай порожні (за останні 8 тижнів). Лише підказка, нічого не змінює.
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus, Schedule
from .utils import today_local

WD = ("понеділок", "вівторок", "середу", "четвер", "пʼятницю", "суботу", "неділю")
WD_NOM = ("понеділок", "вівторок", "середа", "четвер", "пʼятниця", "субота", "неділя")


async def weak_windows(weeks: int = 8, threshold: float = 0.12, min_len: int = 2) -> list[tuple[int, int, int, int]]:
    """[(день тижня, з години, до години, % зайнятості)]. Порожньо, якщо даних замало."""
    today = today_local()
    since = today - timedelta(days=7 * weeks)
    async with session_scope() as session:
        sched = (await session.execute(select(Schedule).where(Schedule.is_day_off.is_(False)))).scalars().all()
        bookings = (
            await session.execute(
                select(Booking).where(
                    Booking.date >= since,
                    Booking.date < today,
                    Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING]),
                )
            )
        ).scalars().all()
    if len(bookings) < 10 or not sched:
        return []
    hours: dict[int, tuple[int, int]] = {}
    for s in sched:
        lo, hi = s.start_time.hour, s.end_time.hour + (1 if s.end_time.minute else 0)
        old = hours.get(s.weekday)
        hours[s.weekday] = (min(lo, old[0]), max(hi, old[1])) if old else (lo, hi)
    days_by_wd: dict[int, list] = {}
    d = since
    while d < today:
        days_by_wd.setdefault(d.weekday(), []).append(d)
        d += timedelta(days=1)
    busy: dict[tuple[int, int], int] = {}
    for b in bookings:
        for h in range(b.start_time.hour, b.end_time.hour + (1 if b.end_time.minute else 0)):
            busy[(b.date.weekday(), h)] = busy.get((b.date.weekday(), h), 0) + 1
    result: list[tuple[int, int, int, int]] = []
    for wd, (lo, hi) in hours.items():
        n_days = len(days_by_wd.get(wd, []))
        if n_days < 4:
            continue
        ratios = [(h, busy.get((wd, h), 0) / n_days) for h in range(lo, hi)]
        run: list[tuple[int, float]] = []
        for h, r in ratios + [(hi, 1.0)]:
            if r <= threshold:
                run.append((h, r))
                continue
            if len(run) >= min_len:
                seg = run[:3]
                avg = int(round(100 * sum(x for _, x in seg) / len(seg)))
                result.append((wd, seg[0][0], seg[-1][0] + 1, avg))
            run = []
    result.sort(key=lambda x: (x[3], -(x[2] - x[1])))
    return result[:3]


def announcement(windows: list[tuple[int, int, int, int]]) -> str:
    parts = [f"{WD[wd]} {a:02d}:00–{z:02d}:00" for wd, a, z, _ in windows]
    return (
        "🌿 Є вільні місця на цьому тижні: " + ", ".join(parts) + ".\n"
        "Оберіть зручний час у боті: «📝 Записатися»."
    )
