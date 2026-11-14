# shared/descseed.py — одноразово додає описи послугам, у яких опис порожній. Існуючі описи не чіпає.
from __future__ import annotations

import logging

from sqlalchemy import select

from .database import session_scope
from .desc_data import BY_NAME
from .models import SalonInfo, Service

logger = logging.getLogger(__name__)
MARK = "desc_seeded_v1"


async def seed() -> None:
    try:
        async with session_scope() as session:
            if await session.get(SalonInfo, MARK) is not None:
                return
            rows = (await session.execute(select(Service))).scalars().all()
            n = 0
            for s in rows:
                if not (s.description or "").strip() and s.name in BY_NAME:
                    s.description = BY_NAME[s.name]
                    n += 1
            session.add(SalonInfo(key=MARK, value="1"))
        logger.info("Додано описів послуг: %s", n)
    except Exception:
        logger.exception("Не вдалося додати описи послуг")
