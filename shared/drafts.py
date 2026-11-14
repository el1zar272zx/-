# shared/drafts.py — незавершений запис: нагадуємо один раз і даємо продовжити
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete, select

from .database import session_scope
from .models import BookingDraft
from .utils import now_local

logger = logging.getLogger(__name__)


async def save(user_id: int, service_ids: list[int], city_id: int | None) -> None:
    if not service_ids:
        return
    try:
        now = now_local().replace(tzinfo=None)
        async with session_scope() as session:
            row = await session.get(BookingDraft, user_id)
            ids = ",".join(str(i) for i in service_ids)[:100]
            if row is None:
                session.add(BookingDraft(client_telegram_id=user_id, service_ids=ids, city_id=city_id, updated_at=now))
            else:
                if row.service_ids != ids:
                    row.nudged = False
                row.service_ids, row.city_id, row.updated_at = ids, city_id, now
    except Exception:
        logger.exception("Не вдалося зберегти чернетку запису")


async def clear(user_id: int) -> None:
    try:
        async with session_scope() as session:
            await session.execute(delete(BookingDraft).where(BookingDraft.client_telegram_id == user_id))
    except Exception:
        logger.exception("Не вдалося прибрати чернетку запису")


async def get(user_id: int) -> BookingDraft | None:
    async with session_scope() as session:
        return await session.get(BookingDraft, user_id)


async def due(after_hours: int = 3, max_hours: int = 48) -> list[BookingDraft]:
    now = now_local().replace(tzinfo=None)
    async with session_scope() as session:
        res = await session.execute(
            select(BookingDraft).where(
                BookingDraft.nudged.is_(False),
                BookingDraft.updated_at <= now - timedelta(hours=after_hours),
                BookingDraft.updated_at >= now - timedelta(hours=max_hours),
            )
        )
        return list(res.scalars().all())


async def mark_nudged(user_id: int) -> None:
    async with session_scope() as session:
        row = await session.get(BookingDraft, user_id)
        if row is not None:
            row.nudged = True


async def cleanup(days: int = 5) -> None:
    limit = now_local().replace(tzinfo=None) - timedelta(days=days)
    async with session_scope() as session:
        await session.execute(delete(BookingDraft).where(BookingDraft.updated_at < limit))
