# shared/together.py — «часто беруть разом»: підказки з реальної історії записів
from __future__ import annotations

from collections import Counter

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus, Service

MIN_TIMES = 2  # скільки разів пару вже обирали разом


async def suggest(cart_ids: list[int], limit: int = 2) -> list[Service]:
    """Послуги, які найчастіше обирали разом із вмістом кошика."""
    async with session_scope() as session:
        services = (
            await session.execute(select(Service).where(Service.is_active.is_(True)))
        ).scalars().all()
        titles = (
            await session.execute(
                select(Booking.service_title).where(
                    Booking.service_title.is_not(None),
                    Booking.status == BookingStatus.CONFIRMED,
                )
            )
        ).scalars().all()
    by_name = {s.name: s for s in services}
    in_cart = {by_name_id.name for by_name_id in services if by_name_id.id in cart_ids}
    if not in_cart:
        return []
    counts: Counter[str] = Counter()
    for title in titles:
        parts = [p.strip() for p in title.split(" + ")]
        if in_cart & set(parts):
            for p in parts:
                if p not in in_cart and p in by_name:
                    counts[p] += 1
    return [by_name[n] for n, c in counts.most_common(limit) if c >= MIN_TIMES]
