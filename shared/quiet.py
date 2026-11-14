# shared/quiet.py — «тихі години»: клієнт обирає, коли його не турбувати
from __future__ import annotations

from sqlalchemy import select

from .database import session_scope
from .models import Client
from .utils import now_local

OPTIONS = {
    "off": ("Без обмежень", None),
    "21-9": ("Не турбувати з 21:00 до 9:00", "21-9"),
    "22-10": ("Не турбувати з 22:00 до 10:00", "22-10"),
    "20-11": ("Не турбувати з 20:00 до 11:00", "20-11"),
}


def label(value: str | None) -> str:
    for text, v in OPTIONS.values():
        if v == value:
            return text
    return "Без обмежень"


def in_quiet(value: str | None, hour: int) -> bool:
    if not value:
        return False
    try:
        a, b = (int(x) for x in value.split("-"))
    except ValueError:
        return False
    return hour >= a or hour < b if a > b else a <= hour < b


async def quiet_ids() -> set[int]:
    """Telegram-id клієнтів, яких зараз не можна турбувати."""
    hour = now_local().hour
    async with session_scope() as session:
        rows = (
            await session.execute(select(Client.telegram_id, Client.quiet).where(Client.quiet.is_not(None)))
        ).all()
    return {tid for tid, q in rows if in_quiet(q, hour)}
