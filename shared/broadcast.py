# shared/broadcast.py — розсилки: аудиторії, сезонні ідеї, відправка, відкладені
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus, Client, ScheduledBroadcast, Service
from .quiet import quiet_ids
from .utils import esc, now_local, today_local

logger = logging.getLogger(__name__)

SEGMENTS = {
    "all": "Усі клієнти",
    "inactive": "Давно не були (понад 60 днів)",
    "bday": "Іменинники цього місяця",
    "new": "Нові (один візит)",
    "regular": "Постійні (3+ візити)",
}

IDEAS = [
    ("🍂 Осінь: спина після дачі", "Доброго дня! 🍂 Осінь — час повернути легкість спині й шиї після літніх турбот. Є вільні години на цьому тижні, обирайте зручний час у меню «📝 Записатися» 💛"),
    ("❄️ Зима: тепло й розслаблення", "Холодні вечори — привід подбати про себе 🌿 Розслаблювальний масаж допоможе скинути напругу. Запис відкритий у боті."),
    ("🌸 Весна: час освіжитися", "Весна прийшла! 🌸 Саме час підтримати шкіру й тіло. Подивіться послуги й оберіть зручний час у боті."),
    ("☀️ Літо: ноги й набряки", "Літня спека — важкі ноги? ☀️ Пресотерапія й масаж допомагають. Є вільні години, записуйтесь у боті."),
    ("🎁 Свято: запис заздалегідь", "Наближаються свята 🎉 Вільного часу стає менше, тож запишіться заздалегідь, поки є зручні години."),
    ("🔥 Акція", "Діє акційна ціна на послугу! 🔥 Подробиці й запис у меню «💆 Послуги»."),
]


async def segment_ids(segment: str) -> list[int]:
    """Telegram-id клієнтів сегмента (лише реальні чати)."""
    today = today_local()
    async with session_scope() as session:
        clients = (await session.execute(select(Client))).scalars().all()
        ids: list[int]
        if segment == "all":
            ids = [c.telegram_id for c in clients]
        elif segment == "bday":
            ids = [c.telegram_id for c in clients if c.birthday and c.birthday.month == today.month]
        elif segment.startswith("svc:") and segment[4:].isdigit():
            rows = (
                await session.execute(
                    select(Booking.client_telegram_id).where(
                        Booking.service_id == int(segment[4:]),
                        Booking.status == BookingStatus.CONFIRMED,
                    )
                )
            ).scalars().all()
            ids = sorted(set(int(x) for x in rows))
        elif segment in ("inactive", "new", "regular"):
            visits: dict[int, int] = {}
            last: dict[int, object] = {}
            rows = (
                await session.execute(
                    select(Booking.client_telegram_id, Booking.date).where(
                        Booking.status == BookingStatus.CONFIRMED, Booking.date <= today
                    )
                )
            ).all()
            for tid, d in rows:
                visits[tid] = visits.get(tid, 0) + 1
                if tid not in last or d > last[tid]:
                    last[tid] = d
            limit = today - timedelta(days=60)
            if segment == "inactive":
                ids = [c.telegram_id for c in clients if c.telegram_id in last and last[c.telegram_id] <= limit]
            elif segment == "new":
                ids = [c.telegram_id for c in clients if visits.get(c.telegram_id, 0) == 1]
            else:
                ids = [c.telegram_id for c in clients if visits.get(c.telegram_id, 0) >= 3]
        else:
            ids = []
    return [i for i in ids if i > 0]


async def segment_label(segment: str) -> str:
    if segment in SEGMENTS:
        return SEGMENTS[segment]
    if segment.startswith("svc:") and segment[4:].isdigit():
        async with session_scope() as session:
            svc = await session.get(Service, int(segment[4:]))
        return f"Ходили на «{svc.name}»" if svc else "Послуга"
    return segment


async def send_many(bot: Bot, ids: list[int], text: str) -> tuple[int, int, int]:
    """(доставлено, не доставлено, пропущено через тихі години)."""
    quiet = await quiet_ids()
    ok = fail = skipped = 0
    for chat_id in ids:
        if chat_id in quiet:
            skipped += 1
            continue
        for _ in (1, 2):
            try:
                await bot.send_message(chat_id, esc(text))
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (TelegramAPIError, OSError, asyncio.TimeoutError):
                fail += 1
                break
        await asyncio.sleep(0.06)  # ліміт Telegram ~30 повідомлень/с
    return ok, fail, skipped


async def schedule(text: str, segment: str, run_at: datetime) -> int:
    async with session_scope() as session:
        row = ScheduledBroadcast(text=text, segment=segment, run_at=run_at)
        session.add(row)
        await session.flush()
        return row.id


async def due() -> list[ScheduledBroadcast]:
    now = now_local().replace(tzinfo=None)
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(ScheduledBroadcast).where(
                        ScheduledBroadcast.done.is_(False), ScheduledBroadcast.run_at <= now
                    )
                )
            ).scalars().all()
        )


async def pending() -> list[ScheduledBroadcast]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(ScheduledBroadcast)
                    .where(ScheduledBroadcast.done.is_(False))
                    .order_by(ScheduledBroadcast.run_at)
                )
            ).scalars().all()
        )


async def mark_done(bid: int) -> None:
    async with session_scope() as session:
        row = await session.get(ScheduledBroadcast, bid)
        if row is not None:
            row.done = True


async def delete(bid: int) -> None:
    async with session_scope() as session:
        row = await session.get(ScheduledBroadcast, bid)
        if row is not None:
            await session.delete(row)
