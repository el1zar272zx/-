# shared/seasonal.py — сезонні й особисті привітання на головному екрані + «як зазвичай»
from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus, Client, Service
from .utils import today_local


def greeting_line(client: Client, today: date | None = None) -> str:
    today = today or today_local()
    b = client.birthday
    if b and (b.month, b.day) == (today.month, today.day):
        return "🎂 Вітаю вас з днем народження! Нехай буде легко й тепло 💛"
    if b and b.month == today.month and 0 < b.day - today.day <= 7:
        return "🎂 Незабаром ваш день народження, а подарунок — гарний настрій 💛"
    md = (today.month, today.day)
    if (12, 20) <= md or md <= (1, 10):
        return "✨ З наступаючими святами! Запишіться заздалегідь, поки є вільний час."
    if md == (3, 8) or (3, 1) <= md <= (3, 8):
        return "🌷 З весняним святом! Подбайте про себе."
    month_lines = {
        9: "🍂 Осінь: час повернути легкість спині й шиї.",
        10: "🍂 Осінь: гарний час розслабитись і відновитись.",
        11: "🍁 Холодні вечори — привід подбати про себе.",
        1: "❄️ Зима: тепла хвилина для себе не завадить.",
        2: "❄️ Підтримайте тіло й шкіру до весни.",
        3: "🌸 Весна: час освіжитися.",
        4: "🌸 Весна: легкість для тіла й шкіри.",
        5: "🌿 Травень: гарний час для догляду.",
        6: "☀️ Літо: легкість для ніг і спини.",
        7: "☀️ Літо: відпочиньте душею й тілом.",
        8: "☀️ Серпень: підготуйте шкіру й тіло до осені.",
    }
    return month_lines.get(today.month, "")


async def usual_service(telegram_id: int) -> Service | None:
    """Послуга, яку клієнт брав найчастіше (від 3 підтверджених візитів)."""
    async with session_scope() as session:
        ids = (
            await session.execute(
                select(Booking.service_id).where(
                    Booking.client_telegram_id == telegram_id,
                    Booking.status == BookingStatus.CONFIRMED,
                )
            )
        ).scalars().all()
        if len(ids) < 3:
            return None
        sid, _ = Counter(ids).most_common(1)[0]
        svc = await session.get(Service, sid)
    return svc if svc is not None and svc.is_active else None
