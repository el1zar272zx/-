# shared/autoreply.py — автовідповідь, коли майстер поза робочим часом
from __future__ import annotations

from datetime import datetime, timedelta

from .care import get_flag
from .database import session_scope
from .services import get_active_cities, get_day_hours, get_working_dates
from .utils import now_local, today_local


async def off_hours_note() -> str | None:
    """Текст-дописка «майстер відповість у робочий час» або None, якщо зараз працюємо/вимкнено."""
    if not await get_flag("autoreply_on"):
        return None
    now = now_local()
    today = today_local()
    async with session_scope() as session:
        cities = await get_active_cities(session)
        if not cities:
            return None
        city = cities[0].id
        hours = await get_day_hours(session, city, today)
        if hours is not None:
            start, end = hours
            cur = now.time()
            if start <= cur < end:
                return None
            if cur < start:
                return f"Зараз майстер поза робочим часом. Вона відповість сьогодні з {start:%H:%M} 🌿"
        nxt = await get_working_dates(session, city, start=today + timedelta(days=1), days=14)
        if not nxt:
            return "Зараз майстер поза робочим часом. Вона відповість, щойно повернеться 🌿"
        day = nxt[0]
        h = await get_day_hours(session, city, day)
    when = "завтра" if day == today + timedelta(days=1) else day.strftime("%d.%m")
    return f"Зараз майстер поза робочим часом. Вона відповість {when}" + (f" з {h[0]:%H:%M}" if h else "") + " 🌿"
