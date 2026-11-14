# shared/jobs2.py — додаткові фонові завдання: підсумок дня, досягнення, порожні години, поради, чернетки
from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from . import actionlog, drafts, emptyhours, library
from .database import session_scope
from .models import AdminTask, Booking, BookingStatus, Client, SalonInfo
from .services import get_active_admin_ids, get_bookings_for_date
from .utils import esc, today_local

logger = logging.getLogger(__name__)


async def _admins() -> list[int]:
    async with session_scope() as session:
        return await get_active_admin_ids(session)


async def _flag(key: str) -> bool:
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
    return bool(row and row.value == "1")


async def set_flag(key: str, on: bool) -> None:
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
        if row is None:
            session.add(SalonInfo(key=key, value="1" if on else "0"))
        else:
            row.value = "1" if on else "0"


async def tips_enabled() -> bool:
    return await _flag("tips_on")


async def tasks_text() -> str:
    async with session_scope() as session:
        rows = (
            await session.execute(select(AdminTask).where(AdminTask.done.is_(False)).order_by(AdminTask.id))
        ).scalars().all()
    if not rows:
        return ""
    return "\n\n📋 <b>Ваші завдання</b>\n" + "\n".join(f"• {esc(t.text)}" for t in rows[:10])


async def day_summary(admin_bot: Bot | None) -> None:
    """Увечері: скільки зроблено за день і що завтра."""
    if admin_bot is None:
        return
    try:
        day = today_local()
        async with session_scope() as session:
            rows = (await session.execute(select(Booking).where(Booking.date == day))).scalars().all()
            tomorrow = await get_bookings_for_date(session, day + timedelta(days=1))
        done = [b for b in rows if b.status == BookingStatus.CONFIRMED]
        noshow = [b for b in rows if b.status == BookingStatus.NO_SHOW]
        if not done and not noshow:
            return
        total = sum(b.price or 0 for b in done)
        paid = sum(b.price or 0 for b in done if b.is_paid)
        lines = [
            "🌙 <b>Підсумок дня</b>",
            f"✅ Візитів: {len(done)}",
            f"💰 Сума: {total} грн" + (f" (з них позначено оплаченими: {paid} грн)" if paid else ""),
        ]
        if noshow:
            lines.append(f"🙈 Не прийшли: {len(noshow)}")
        lines.append(f"📅 Завтра записів: {len(tomorrow)}")
        lines.append("\nДякую за роботу! Відпочивайте 🌿")
        for admin_id in await _admins():
            try:
                await admin_bot.send_message(admin_id, "\n".join(lines))
            except TelegramAPIError:
                pass
    except Exception:
        logger.exception("Помилка підсумку дня")


async def achievements(admin_bot: Bot | None) -> None:
    if admin_bot is None:
        return
    try:
        from . import achievements as ach

        texts = await ach.new_messages()
        if not texts:
            return
        for admin_id in await _admins():
            for t in texts:
                try:
                    await admin_bot.send_message(admin_id, t)
                except TelegramAPIError:
                    pass
    except Exception:
        logger.exception("Помилка досягнень")


async def empty_hours(admin_bot: Bot | None) -> None:
    """Раз на тиждень: показує майстру порожні години й пропонує розсилку (лише після її підтвердження)."""
    if admin_bot is None:
        return
    try:
        windows = await emptyhours.weak_windows()
        if not windows:
            return
        lines = ["🔍 <b>Зазвичай порожні години</b> (за останні 8 тижнів):"]
        for wd, a, z, pct in windows:
            lines.append(f"• {emptyhours.WD_NOM[wd]} {a:02d}:00–{z:02d}:00 (зайнято ~{pct}%)")
        lines.append("\nМожу запропонувати клієнтам вільні вікна цього тижня. Нічого не піде без вашого підтвердження.")
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👀 Переглянути текст розсилки", callback_data="eh_preview")],
                [InlineKeyboardButton(text="Не потрібно", callback_data="adm_menu")],
            ]
        )
        for admin_id in await _admins():
            try:
                await admin_bot.send_message(admin_id, "\n".join(lines), reply_markup=markup)
            except TelegramAPIError:
                pass
    except Exception:
        logger.exception("Помилка аналізу порожніх годин")


async def weekly_tip(client_bot: Bot | None) -> None:
    """Порада тижня клієнтам, які не відмовились. За замовчуванням вимкнено: вмикає майстер."""
    if client_bot is None or not await tips_enabled():
        return
    try:
        tip = await library.next_tip()
        if tip is None:
            return
        from .quiet import quiet_ids

        quiet = await quiet_ids()
        async with session_scope() as session:
            clients = (await session.execute(select(Client).where(Client.tips_off.is_(False)))).scalars().all()
        text = f"💡 <b>Порада тижня</b>\n\n<b>{esc(tip.title)}</b>\n{esc(tip.text)}"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔕 Більше не надсилати поради", callback_data="tips_stop")]]
        )
        import asyncio

        for c in clients:
            if c.telegram_id <= 0 or c.telegram_id in quiet:
                continue
            try:
                await client_bot.send_message(c.telegram_id, text, reply_markup=markup)
            except TelegramAPIError:
                pass
            await asyncio.sleep(0.06)
    except Exception:
        logger.exception("Помилка поради тижня")


async def drafts_nudge(client_bot: Bot | None) -> None:
    """Якщо клієнт не завершив запис — один раз м'яко нагадуємо."""
    if client_bot is None:
        return
    try:
        from .quiet import quiet_ids

        quiet = await quiet_ids()
        for d in await drafts.due():
            await drafts.mark_nudged(d.client_telegram_id)
            if d.client_telegram_id in quiet:
                continue
            try:
                await client_bot.send_message(
                    d.client_telegram_id,
                    "🌿 Ви не завершили запис. Продовжимо з того ж місця?",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="➡️ Продовжити", callback_data="draft_go")]]
                    ),
                )
            except TelegramAPIError:
                pass
        await drafts.cleanup()
        await actionlog.cleanup()
    except Exception:
        logger.exception("Помилка нагадування про незавершений запис")
