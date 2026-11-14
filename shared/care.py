# shared/care.py — повторні візити (за типом процедури), опитування про самопочуття, перемикачі
from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus, SalonInfo, Service
from .utils import today_local

logger = logging.getLogger(__name__)

# Орієнтири за відкритими джерелами (ботокс ~ кожні 3–4 міс., філери губ ~ 4–12 міс.,
# масаж за самопочуттям від тижня до місяця). Майстер змінює їх у «🔔 Повторні візити».
# (ключові слова в назві, recall_days, followup_days)
DEFAULTS: list[tuple[tuple[str, ...], int, int]] = [
    (("ботокс", "ботулін", "botox", "диспорт", "ксеомін"), 105, 4),
    (("губ", "філер", "контурн", "гіалурон"), 180, 3),
    (("масаж",), 14, 1),
]


async def get_flag(key: str, default: bool = True) -> bool:
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
    return default if row is None else row.value == "1"


async def set_flag(key: str, value: bool) -> None:
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
        if row is None:
            session.add(SalonInfo(key=key, value="1" if value else "0"))
        else:
            row.value = "1" if value else "0"


async def seed_care_defaults() -> None:
    """Одноразово виставляє типові інтервали для послуг, назви яких збігаються."""
    async with session_scope() as session:
        if await session.get(SalonInfo, "care_seeded") is not None:
            return
        for svc in (await session.execute(select(Service))).scalars().all():
            name = svc.name.casefold()
            for keywords, recall, follow in DEFAULTS:
                if any(k in name for k in keywords):
                    if svc.recall_days is None:
                        svc.recall_days = recall
                    if svc.followup_days is None:
                        svc.followup_days = follow
                    break
        session.add(SalonInfo(key="care_seeded", value="1"))


async def personal_interval(session, telegram_id: int, service: Service) -> int:
    """Інтервал для конкретного клієнта: його власний ритм (від 3 візитів), у межах 0.5–1.5 від норми."""
    base = service.recall_days or 0
    if base <= 0:
        return 0
    res = await session.execute(
        select(Booking.date)
        .where(
            Booking.client_telegram_id == telegram_id,
            Booking.service_id == service.id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.date < today_local(),
        )
        .order_by(Booking.date)
    )
    dates = [d for d in res.scalars().all()]
    if len(dates) < 3:
        return base
    gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
    if len(gaps) < 2:
        return base
    mine = int(statistics.median(gaps))
    return max(5, int(base * 0.5), min(mine, int(base * 1.5)))


def interval_label(days: int) -> str:
    if days % 30 == 0 and days >= 60:
        return f"{days // 30} міс."
    if days >= 60:
        return f"{round(days / 30)} міс."
    if days % 7 == 0:
        return f"{days // 7} тиж."
    return f"{days} дн."


async def next_visit_hint(booking: Booking) -> tuple[str, int] | None:
    """Текст для клієнта «коли наступний сеанс» і id послуги; None, якщо вимкнено."""
    if not await get_flag("recall_on"):
        return None
    async with session_scope() as session:
        svc = await session.get(Service, booking.service_id)
        if svc is None or not svc.recall_days:
            return None
        days = await personal_interval(session, booking.client_telegram_id, svc)
    if days <= 0:
        return None
    when = booking.date + timedelta(days=days)
    return (
        f"🗓 Для «{svc.name}» зазвичай радять повторювати приблизно через {interval_label(days)} "
        f"(орієнтовно {when:%d.%m}). Можу одразу підібрати зручний час.",
        svc.id,
    )


async def due_recalls() -> list[tuple[Booking, Service, int]]:
    """Візити, після яких настав час нагадати про повтор. Старі й вже неактуальні лише закриваються."""
    out: list[tuple[Booking, Service, int]] = []
    today = today_local()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Booking)
                .where(
                    Booking.status == BookingStatus.CONFIRMED,
                    Booking.recall_sent.is_(False),
                    Booking.date < today,
                    Booking.client_telegram_id > 0,
                )
                .order_by(Booking.date.desc())
            )
        ).scalars().all()
        for b in rows:
            svc = await session.get(Service, b.service_id)
            if svc is None or not svc.recall_days:
                b.recall_sent = True
                continue
            later = (
                await session.execute(
                    select(Booking.id).where(
                        Booking.client_telegram_id == b.client_telegram_id,
                        Booking.service_id == b.service_id,
                        Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                        Booking.date > b.date,
                    ).limit(1)
                )
            ).first()
            days = await personal_interval(session, b.client_telegram_id, svc)
            due = b.date + timedelta(days=days)
            if later is not None or today > due + timedelta(days=45):
                b.recall_sent = True  # вже записався або занадто пізно — не турбуємо
            elif today >= due - timedelta(days=2):
                out.append((b, svc, days))
    return out


async def mark_recall_sent(booking_id: int) -> None:
    async with session_scope() as session:
        b = await session.get(Booking, booking_id)
        if b:
            b.recall_sent = True


async def due_followups() -> list[tuple[Booking, Service]]:
    out: list[tuple[Booking, Service]] = []
    today = today_local()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Booking).where(
                    Booking.status == BookingStatus.CONFIRMED,
                    Booking.followup_sent.is_(False),
                    Booking.date < today,
                    Booking.client_telegram_id > 0,
                )
            )
        ).scalars().all()
        for b in rows:
            svc = await session.get(Service, b.service_id)
            if svc is None or svc.followup_days is None:
                b.followup_sent = True
                continue
            due = b.date + timedelta(days=svc.followup_days)
            if today > due + timedelta(days=3):
                b.followup_sent = True
            elif today >= due:
                out.append((b, svc))
    return out


async def mark_followup_sent(booking_id: int) -> None:
    async with session_scope() as session:
        b = await session.get(Booking, booking_id)
        if b:
            b.followup_sent = True
