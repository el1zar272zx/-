# shared/visit_notes.py — нотатки майстра після візиту (можна дописати пізніше)
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus
from .utils import now_local, today_local

KEEP_DAYS = 14


async def last_note(telegram_id: int, exclude_id: int | None = None) -> str | None:
    async with session_scope() as session:
        q = select(Booking.visit_note).where(
            Booking.client_telegram_id == telegram_id, Booking.visit_note.is_not(None)
        )
        if exclude_id is not None:
            q = q.where(Booking.id != exclude_id)
        res = await session.execute(q.order_by(Booking.date.desc(), Booking.start_time.desc()).limit(1))
        return res.scalars().first()


async def pending_notes() -> list[Booking]:
    """Минулі підтверджені візити без нотатки (за останні 14 днів)."""
    now = now_local()
    today = today_local()
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Booking)
                .where(
                    Booking.status == BookingStatus.CONFIRMED,
                    Booking.note_done.is_(False),
                    Booking.date >= today - timedelta(days=KEEP_DAYS),
                    Booking.date <= today,
                )
                .order_by(Booking.date.desc(), Booking.start_time.desc())
            )
        ).scalars().all()
    return [b for b in rows if b.date < today or b.end_time <= now.time()]


async def save_note(booking_id: int, text: str | None) -> bool:
    async with session_scope() as session:
        b = await session.get(Booking, booking_id)
        if b is None:
            return False
        if text:
            b.visit_note = text[:500]
        b.note_done = True
        return True
