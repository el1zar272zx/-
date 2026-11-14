# shared/replyeta.py — коли майстер, ймовірно, відповість (за зайнятістю й історією відповідей)
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from statistics import median

from sqlalchemy import select

from .database import session_scope
from .models import BookingStatus, ChatMessage
from .services import get_active_cities, get_bookings_for_date, get_day_hours
from .utils import now_local, today_local

logger = logging.getLogger(__name__)


async def typical_minutes() -> int | None:
    """Медіана часу відповіді майстра за останні питання (хвилини), якщо даних достатньо."""
    since = now_local().replace(tzinfo=None) - timedelta(days=60)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ChatMessage).where(ChatMessage.created_at >= since).order_by(ChatMessage.id)
            )
        ).scalars().all()
    waits: list[float] = []
    pending: dict[int, datetime] = {}
    for m in rows:
        ts = m.created_at.replace(tzinfo=None) if m.created_at.tzinfo else m.created_at
        if m.direction == "in":
            pending.setdefault(m.client_telegram_id, ts)
        elif m.client_telegram_id in pending:
            wait = (ts - pending.pop(m.client_telegram_id)).total_seconds() / 60
            if 0 <= wait <= 720:
                waits.append(wait)
    if len(waits) < 5:
        return None
    return max(5, int(median(waits)))


async def eta_note() -> str | None:
    """Рядок для клієнта, якщо майстер зараз працює; поза робочим часом повертає None."""
    try:
        now = now_local()
        today = today_local()
        async with session_scope() as session:
            cities = await get_active_cities(session)
            if not cities:
                return None
            hours = await get_day_hours(session, cities[0].id, today)
            if hours is None:
                return None
            start, end = hours
            cur = now.time()
            if not (start <= cur < end):
                return None
            bookings = [
                b
                for b in await get_bookings_for_date(session, today)
                if b.status == BookingStatus.CONFIRMED
            ]
        busy_until = None
        for b in sorted(bookings, key=lambda x: x.start_time):
            if b.start_time <= cur < b.end_time:
                busy_until = b.end_time
            elif busy_until is not None and b.start_time <= busy_until:
                busy_until = b.end_time
        if busy_until is not None:
            return f"⏱ Зараз майстер із клієнтом. Відповідь буде орієнтовно після {busy_until:%H:%M} 🌿"
        typ = await typical_minutes()
        if typ is not None:
            low = max(5, (typ // 5) * 5)
            return f"⏱ Майстер на звʼязку. Зазвичай відповідає приблизно за {low}–{low + 15} хвилин 🌿"
        return "⏱ Майстер на звʼязку й зазвичай відповідає протягом 15–30 хвилин 🌿"
    except Exception:
        logger.exception("Не вдалося порахувати час відповіді")
        return None
