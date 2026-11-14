# shared/works.py — галерея «Приклади робіт» для послуг
from __future__ import annotations

import io
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import WorkPhoto

logger = logging.getLogger(__name__)

MAX_PER_SERVICE = 8  # до 10 можна слати одним альбомом; 8 достатньо й не роздуває базу


def compress(data: bytes) -> bytes:
    """Зменшує фото до 1280 px і стискає в JPEG: швидше вантажиться клієнтам, менша база й бекап."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.thumbnail((1280, 1280))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    except Exception:
        logger.warning("Не вдалося стиснути фото робіт, зберігаю як є")
        return data


async def list_works(session: AsyncSession, service_id: int) -> list[WorkPhoto]:
    res = await session.execute(
        select(WorkPhoto).where(WorkPhoto.service_id == service_id).order_by(WorkPhoto.id)
    )
    return list(res.scalars().all())


async def count_works(session: AsyncSession, service_id: int) -> int:
    res = await session.execute(
        select(func.count()).select_from(WorkPhoto).where(WorkPhoto.service_id == service_id)
    )
    return int(res.scalar() or 0)


async def add_work(session: AsyncSession, service_id: int, data: bytes, caption: str | None) -> WorkPhoto:
    work = WorkPhoto(service_id=service_id, data=compress(data), caption=(caption or None))
    session.add(work)
    await session.flush()
    return work


async def delete_work(session: AsyncSession, work_id: int) -> int | None:
    """Видаляє фото; повертає id послуги (щоб повернутися до її списку)."""
    work = await session.get(WorkPhoto, work_id)
    if work is None:
        return None
    sid = work.service_id
    await session.execute(delete(WorkPhoto).where(WorkPhoto.id == work_id))
    return sid
