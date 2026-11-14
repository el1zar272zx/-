# client_bot/middleware.py
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from shared.database import session_scope
from shared.services import get_client

from .keyboards import (
    BTN_BOOK,
    BTN_FAQ,
    BTN_ASK,
    BTN_INFO,
    BTN_QUIZ,
    BTN_MY,
    BTN_PROFILE,
    BTN_SERVICES,
    BTN_CARE,
    BTN_USEFUL,
    T,
    contact_kb,
)

MENU_TEXTS = set().union(
    *(T(b) for b in (BTN_BOOK, BTN_SERVICES, BTN_MY, BTN_PROFILE, BTN_INFO, BTN_FAQ, BTN_QUIZ, BTN_ASK, BTN_USEFUL, BTN_CARE))
)
BOOKING_PREFIXES = ("view_book_", "repeat_last", "mybnext_", "recall_", "cart_dates")

ASK_CONTACT = (
    "Щоб записатися, поділіться, будь ласка, своїм контактом: натисніть кнопку "
    "«📱 Поділитися контактом» внизу.\n"
    "Номер збережеться, і повторно його вводити не доведеться."
)


class RegistrationMiddleware(BaseMiddleware):
    """Поки клієнт не поділився контактом, пускає лише /start і сам контакт."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return await handler(event, data)

        if isinstance(event, Message):
            state = data.get("state")
            if state is not None and event.text in MENU_TEXTS:
                await state.clear()  # кнопки меню завжди працюють, навіть посеред введення
            if event.contact is not None:
                return await handler(event, data)
            first = (event.text or "").split(maxsplit=1)
            if first and first[0].split("@")[0] == "/start":
                return await handler(event, data)

        if isinstance(event, CallbackQuery) and (event.data or "").startswith("setlang_"):
            return await handler(event, data)  # вибір мови доступний ще до реєстрації
        async with session_scope() as session:
            client = await get_client(session, user.id)
        if client is not None:
            wants_booking = (
                isinstance(event, Message) and event.text in T(BTN_BOOK)
            ) or (
                isinstance(event, CallbackQuery)
                and (event.data or "").startswith(BOOKING_PREFIXES)
            )
            if client.booking_blocked and wants_booking:
                from shared import salon

                text = "Самостійний запис для вас вимкнено. Будь ласка, зателефонуйте майстру"
                text += f": {salon.info('phone')}" if salon.info("phone") else "."
                if isinstance(event, CallbackQuery):
                    await event.answer(text, show_alert=True)
                else:
                    await event.answer(text)
                return None
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(ASK_CONTACT, reply_markup=contact_kb())
        elif isinstance(event, Message):
            await event.answer(ASK_CONTACT, reply_markup=contact_kb())
        return None
