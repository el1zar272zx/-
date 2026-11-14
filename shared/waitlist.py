# shared/waitlist.py — сповіщення списку очікування, коли звільнилось місце
from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .database import session_scope
from .notifications_soft import format_waitlist_free
from .services import pop_ready_waitlist
from .utils import fmt_date_long

logger = logging.getLogger(__name__)


async def notify_waitlist(client_bot: Bot | None, day: date) -> int:
    """Пише клієнтам, які чекали на цю дату, якщо тепер є вільний слот. Повертає кількість."""
    if client_bot is None:
        return 0
    try:
        async with session_scope() as session:
            entries = await pop_ready_waitlist(session, day)
    except Exception:
        logger.exception("Не вдалося обробити список очікування на %s", day)
        return 0
    sent = 0
    for e in entries:
        try:
            await client_bot.send_message(
                e.client_telegram_id, format_waitlist_free(fmt_date_long(day))
            )
            sent += 1
        except TelegramAPIError:
            logger.warning("Очікування: не доставлено клієнту %s", e.client_telegram_id)
    return sent
