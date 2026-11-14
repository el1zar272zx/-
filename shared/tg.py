# shared/tg.py — дрібні хелпери для aiogram
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
)

logger = logging.getLogger(__name__)

Event = CallbackQuery | Message


async def safe_edit(
    call: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Редагує повідомлення з кнопкою. Якщо воно не змінилось — тихо ігнорує,
    якщо редагувати неможливо (старе/видалене/не текстове) — надсилає нове.
    """
    message = call.message
    if message is None:
        await call.bot.send_message(call.from_user.id, text, reply_markup=reply_markup)
        return
    if getattr(message, "photo", None):  # фото не редагується як текст: прибираємо й пишемо нове
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await call.bot.send_message(call.from_user.id, text, reply_markup=reply_markup)
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            return
        logger.info("edit_text failed (%s), sending a new message", e)
        await message.answer(text, reply_markup=reply_markup)


async def respond(
    event: Event,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> None:
    """Для кнопки під повідомленням — редагує його, для тексту з нижньої клавіатури — надсилає нове."""
    if isinstance(event, CallbackQuery):
        await safe_edit(event, text, reply_markup)  # type: ignore[arg-type]
    else:
        await event.answer(text, reply_markup=reply_markup)


async def ack(event: Event, text: str | None = None, alert: bool = False) -> None:
    """Відповідь на callback (одна на кнопку). Для звичайного повідомлення — показує текст, якщо він є."""
    if isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=alert)
    elif text:
        await event.answer(text)
