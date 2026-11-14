# shared/achievements.py — маленькі святкові повідомлення для майстра
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus, SalonInfo
from .utils import now_local

logger = logging.getLogger(__name__)

COFFEE = (
    "Напишіть синові: нехай купить вам каву ☕",
    "Повідомте синові, що сьогодні кава за його рахунок ☕",
    "Скажіть синові, що такий результат заслуговує на каву й тістечко ☕🍰",
    "Час написати синові: кава на ньому ☕",
    "Нехай син пригостить вас кавою: ви її заробили ☕",
)
LEVELS = {
    "visits": (10, 50, 100, 250, 500, 1000),
    "clients": (10, 25, 50, 100, 200, 500),
    "hours": (50, 100, 250, 500, 1000),
}
TITLES = {
    "visits": "🎉 {n}-й візит у вашій історії!",
    "clients": "👥 У вас уже {n} клієнтів!",
    "hours": "⏱ {n} годин роботи з клієнтами!",
}


async def stats() -> dict[str, int]:
    now = now_local().replace(tzinfo=None)
    async with session_scope() as session:
        rows = (
            await session.execute(select(Booking).where(Booking.status == BookingStatus.CONFIRMED))
        ).scalars().all()
    done = [b for b in rows if datetime.combine(b.date, b.end_time) <= now]
    minutes = sum(
        (b.end_time.hour * 60 + b.end_time.minute) - (b.start_time.hour * 60 + b.start_time.minute) for b in done
    )
    return {
        "visits": len(done),
        "clients": len({b.client_telegram_id for b in done}),
        "hours": minutes // 60,
    }


async def new_messages() -> list[str]:
    """Нові досягнення. Перший запуск лише фіксує те, що вже досягнуто (без повідомлень)."""
    st = await stats()
    texts: list[str] = []
    async with session_scope() as session:
        init = await session.get(SalonInfo, "ach_init")
        first_run = init is None
        if first_run:
            session.add(SalonInfo(key="ach_init", value="1"))
        count = 0
        for kind, steps in LEVELS.items():
            reached = [n for n in steps if st[kind] >= n]
            fresh = []
            for n in reached:
                key = f"ach_{kind}_{n}"
                if await session.get(SalonInfo, key) is None:
                    session.add(SalonInfo(key=key, value="1"))
                    fresh.append(n)
            if fresh and not first_run:
                top = max(fresh)
                coffee = COFFEE[(top + count) % len(COFFEE)]
                texts.append(f"{TITLES[kind].format(n=top)}\n{coffee}")
                count += 1
    return texts
