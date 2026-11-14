# client_bot/main.py
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from shared.config import settings
from shared.scheduler import NotificationScheduler

from .handlers import setup_client_routers

logger = logging.getLogger(__name__)


def make_bot(token: str) -> Bot:
    from shared.tg_session import make_session

    return Bot(
        token=token,
        session=make_session(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher(admin_bot: Bot | None = None) -> Dispatcher:
    """admin_bot потрібен, щоб повідомляти адміністраторів про нові записи."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data.update(admin_bot=admin_bot)
    dp.include_router(setup_client_routers())
    return dp


async def main() -> None:
    """Запуск лише клієнтського бота. Зазвичай запускайте run.py (обидва боти разом)."""
    from init_db import bootstrap

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [CLIENT] %(levelname)s: %(name)s: %(message)s",
    )
    if not settings.client_bot_token:
        raise RuntimeError("CLIENT_BOT_TOKEN не задано в .env")

    await bootstrap()
    bot = make_bot(settings.client_bot_token)
    admin_bot = make_bot(settings.admin_bot_token) if settings.admin_bot_token else None
    dp = build_dispatcher(admin_bot)

    async def send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text)

    scheduler = NotificationScheduler(
        send_message_func=send, client_bot=bot, admin_bot=admin_bot
    )
    scheduler.start()
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()
        if admin_bot:
            await admin_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
