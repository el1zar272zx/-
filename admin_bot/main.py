# admin_bot/main.py
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from shared.config import settings

from .handlers import setup_admin_routers
from .middleware import ActionLogMiddleware, AdminOnlyMiddleware

logger = logging.getLogger(__name__)


def make_bot(token: str) -> Bot:
    from shared.tg_session import make_session

    return Bot(
        token=token,
        session=make_session(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_dispatcher(client_bot: Bot | None = None) -> Dispatcher:
    """client_bot потрібен, щоб надсилати клієнтам підтвердження/відмову."""
    dp = Dispatcher(storage=MemoryStorage())
    dp.workflow_data.update(client_bot=client_bot)
    from shared.throttle import ThrottleMiddleware

    throttle = ThrottleMiddleware()
    dp.message.outer_middleware(throttle)
    dp.callback_query.outer_middleware(throttle)
    guard = AdminOnlyMiddleware()
    dp.message.outer_middleware(guard)
    dp.callback_query.outer_middleware(guard)
    dp.callback_query.outer_middleware(ActionLogMiddleware())
    dp.include_router(setup_admin_routers())
    return dp


async def main() -> None:
    """Запуск лише адмін-бота. Зазвичай запускайте run.py (обидва боти разом)."""
    from init_db import bootstrap

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [ADMIN] %(levelname)s: %(name)s: %(message)s",
    )
    if not settings.admin_bot_token:
        raise RuntimeError("ADMIN_BOT_TOKEN не задано в .env")

    await bootstrap()
    bot = make_bot(settings.admin_bot_token)
    client_bot = make_bot(settings.client_bot_token) if settings.client_bot_token else None
    dp = build_dispatcher(client_bot)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        if client_bot:
            await client_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
