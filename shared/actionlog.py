# shared/actionlog.py — журнал дій адмінів (бачить лише власник)
from __future__ import annotations

import logging

from sqlalchemy import delete, select

from .database import session_scope
from .models import ActionLog

logger = logging.getLogger(__name__)


async def log(actor_id: int, action: str, details: str = "") -> None:
    """Записує дію. Ніколи не кидає помилку: журнал не має ламати основну роботу."""
    try:
        async with session_scope() as session:
            session.add(ActionLog(actor_id=actor_id, action=action[:60], details=(details or "")[:500]))
    except Exception:
        logger.exception("Не вдалося записати в журнал дій")


async def recent(limit: int = 40) -> list[ActionLog]:
    async with session_scope() as session:
        res = await session.execute(select(ActionLog).order_by(ActionLog.id.desc()).limit(limit))
        return list(res.scalars().all())


async def cleanup(keep: int = 2000) -> None:
    """Лишає останні записи, щоб журнал не розростався."""
    try:
        async with session_scope() as session:
            last = await session.scalar(select(ActionLog.id).order_by(ActionLog.id.desc()).offset(keep).limit(1))
            if last:
                await session.execute(delete(ActionLog).where(ActionLog.id <= last))
    except Exception:
        logger.exception("Очищення журналу дій")
