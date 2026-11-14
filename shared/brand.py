# shared/brand.py — назва салону для картинок і постів (налаштовується в «Пост для Instagram»)
from __future__ import annotations

from .database import session_scope
from .models import SalonInfo

DEFAULT = "Масаж · Косметологія"


async def brand() -> str:
    async with session_scope() as session:
        row = await session.get(SalonInfo, "brand")
    return (row.value.strip() if row and row.value.strip() else DEFAULT)
