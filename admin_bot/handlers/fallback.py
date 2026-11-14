# admin_bot/handlers/fallback.py — страховка: кнопки панелі завжди працюють
from __future__ import annotations

import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.services import list_admins

from ..keyboards import admin_reply_kb, main_menu_kb, menu_counts

logger = logging.getLogger(__name__)
router = Router(name="admin_fallback")

MENU_TEXT = "🛠 <b>Адмін-панель</b>\nОберіть розділ:"


@router.message()
async def any_text(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    """Будь-який незрозумілий текст: повертаємо кнопки та меню."""
    await state.clear()
    await msg.answer("Кнопки панелі внизу екрана 👇", reply_markup=admin_reply_kb(is_super))
    await msg.answer(MENU_TEXT, reply_markup=main_menu_kb(is_super, await menu_counts()))


@router.callback_query()
async def stale_callback(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    """Застаріла кнопка (наприклад, після перезапуску бота): відкриваємо меню."""
    await state.clear()
    await call.answer("Меню оновлено")
    if call.message is not None:
        await call.message.answer(MENU_TEXT, reply_markup=main_menu_kb(is_super, await menu_counts()))


async def push_keyboards(admin_bot: Bot) -> None:
    """Після запуску бота повертає постійну клавіатуру всім адміністраторам."""
    try:
        async with session_scope() as session:
            admins = await list_admins(session)
    except Exception:
        logger.exception("Не вдалося отримати список адміністраторів")
        return
    for a in admins:
        try:
            await admin_bot.send_message(
                a.telegram_id,
                "🟢 Бот запущено. Кнопки панелі активні 👇",
                reply_markup=admin_reply_kb(bool(a.is_superadmin)),
            )
        except TelegramAPIError:
            logger.warning("Не вдалося надіслати клавіатуру адміну %s", a.telegram_id)
