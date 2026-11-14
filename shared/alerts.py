# shared/alerts.py — сповіщення власника про помилки (не частіше, ніж раз на 15 хвилин для однакової)
from __future__ import annotations

import logging
import time

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .config import settings

logger = logging.getLogger(__name__)
_last: dict[str, float] = {}
COOLDOWN = 900.0


async def alert_owner(admin_bot: Bot | None, title: str, details: str = "") -> None:
    if admin_bot is None or settings.superadmin_telegram_id is None:
        return
    key = f"{title}|{details[:80]}"
    now = time.monotonic()
    if now - _last.get(key, -COOLDOWN) < COOLDOWN:
        return
    _last[key] = now
    text = f"⚠️ <b>{title}</b>"
    if details:
        text += f"\n<code>{details[:600].replace('<', '&lt;')}</code>"
    text += "\nПодробиці — у файлі logs/bot.log."
    try:
        await admin_bot.send_message(settings.superadmin_telegram_id, text)
    except TelegramAPIError:
        logger.warning("Не вдалося надіслати сповіщення власнику")


FRIENDLY = "Щось пішло не так, але нічого не втрачено. Спробуйте ще раз за хвилину 🌿"


def attach_error_alerts(dp, admin_bot: Bot | None) -> None:
    """Усі необроблені помилки в обробниках: у лог, сповіщення власнику й людська відповідь користувачу."""

    @dp.errors()
    async def _on_error(event) -> bool:  # type: ignore[no-untyped-def]
        exc = event.exception
        logger.error("Необроблена помилка", exc_info=exc)
        await alert_owner(admin_bot, "Помилка в боті", f"{type(exc).__name__}: {exc}")
        try:
            update = event.update
            if update.callback_query is not None:
                await update.callback_query.answer(FRIENDLY, show_alert=True)
            elif update.message is not None:
                await update.message.answer(FRIENDLY)
        except Exception:
            pass  # на callback уже відповіли або чат недоступний: це нормально
        return True
