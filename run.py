# run.py — запускає клієнтський і адмін-бота разом (один процес, один планувальник)
from __future__ import annotations

import asyncio
import logging
import sys

from admin_bot.main import build_dispatcher as build_admin_dispatcher
from admin_bot.main import make_bot
from client_bot.main import build_dispatcher as build_client_dispatcher
from init_db import bootstrap
from shared.config import settings
from shared.database import engine, session_scope
from shared.scheduler import NotificationScheduler
from shared.services import get_active_admin_ids

logger = logging.getLogger("run")


async def setup_bot_profile(bot) -> None:
    """Меню команд, опис і коротке привітання бота (видно в профілі бота в Telegram)."""
    from aiogram.types import BotCommand

    try:
        await bot.set_my_commands(
            [
                BotCommand(command="book", description="📝 Записатися"),
                BotCommand(command="my", description="📆 Мої записи"),
                BotCommand(command="faq", description="❓ Часті питання"),
                BotCommand(command="ask", description="💬 Запитати майстра"),
                BotCommand(command="menu", description="Головне меню"),
            ]
        )
        await bot.set_my_description(
            "🌿 Запис до майстра масажу й косметології в Жовтих Водах.\n\n"
            "• Оберіть послугу, дату й час, а ціну побачите на кожному кроці\n"
            "• Нагадаємо про візит і підкажемо, як підготуватися\n"
            "• Можна змінити чи скасувати запис, не телефонуючи\n\n"
            "Натисніть «Почати» 👇"
        )
        await bot.set_my_short_description("🌿 Онлайн-запис на масаж і косметологію. Ціни, вільний час, нагадування.")
    except Exception:
        logger.warning("Не вдалося налаштувати профіль бота (не критично)", exc_info=True)


async def main() -> int:
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                "logs/bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            ),
        ],
    )

    if not settings.client_bot_token:
        logger.error("CLIENT_BOT_TOKEN не задано в .env — клієнтський бот не запуститься.")
        return 1

    from shared.backup import apply_pending_restore

    restored = apply_pending_restore()
    if restored:
        logger.warning(restored)
    await bootstrap()

    from shared import salon
    from shared.aftercare import seed_aftercare_defaults

    await salon.load_salon_info()
    from shared import namefilter

    await namefilter.load_extra()
    await seed_aftercare_defaults()
    from shared.aftercare import seed_aftercare_v2

    await seed_aftercare_v2()
    from shared import care, health

    await care.seed_care_defaults()
    await salon.ensure_faq_v2()
    from shared import contra, levels, library

    await library.seed()
    await contra.seed()
    await levels.load()
    from shared import descseed

    await descseed.seed()
    start_note = health.startup_report()

    client_bot = make_bot(settings.client_bot_token)
    admin_bot = make_bot(settings.admin_bot_token) if settings.admin_bot_token else None

    async with session_scope() as session:
        admin_ids = await get_active_admin_ids(session)
    if admin_bot is None:
        logger.warning(
            "ADMIN_BOT_TOKEN не задано: працює лише клієнтський бот, "
            "адміністратор не отримуватиме заявки."
        )
    elif not admin_ids:
        logger.warning(
            "Немає жодного адміністратора. Вкажіть SUPERADMIN_TELEGRAM_ID в .env "
            "(свій ID покаже адмін-бот, якщо йому написати) і перезапустіть."
        )

    from shared.alerts import alert_owner, attach_error_alerts
    from shared.i18n import install_translation

    install_translation(client_bot)  # російська версія для клієнтів з мовою ru
    client_dp = build_client_dispatcher(admin_bot=admin_bot)
    attach_error_alerts(client_dp, admin_bot)

    async def send_message(chat_id: int, text: str) -> None:
        await client_bot.send_message(chat_id, text)

    scheduler = NotificationScheduler(
        send_message_func=send_message, client_bot=client_bot, admin_bot=admin_bot
    )
    scheduler.start()
    if admin_bot is not None:
        from admin_bot.handlers.fallback import push_keyboards

        await push_keyboards(admin_bot)

    if admin_bot is not None and settings.superadmin_telegram_id:
        try:
            await admin_bot.send_message(settings.superadmin_telegram_id, start_note)
        except Exception:
            logger.warning("Не вдалося надіслати повідомлення про запуск")

    await setup_bot_profile(client_bot)

    tasks = [
        asyncio.create_task(
            client_dp.start_polling(
                client_bot,
                allowed_updates=client_dp.resolve_used_update_types(),
                handle_signals=False,
            ),
            name="client-bot",
        )
    ]
    if admin_bot is not None:
        admin_dp = build_admin_dispatcher(client_bot=client_bot)
        attach_error_alerts(admin_dp, admin_bot)
        tasks.append(
            asyncio.create_task(
                admin_dp.start_polling(
                    admin_bot,
                    allowed_updates=admin_dp.resolve_used_update_types(),
                    handle_signals=False,
                ),
                name="admin-bot",
            )
        )

    logger.info("Боти запущено. Зупинка — Ctrl+C.")
    try:
        await asyncio.gather(*tasks)
    except Exception as exc:
        logger.exception("Бот зупинився через помилку")
        await alert_owner(admin_bot, "Бот зупинився", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        health.mark_clean_exit()
        scheduler.shutdown()
        for task in tasks:
            task.cancel()
        await client_bot.session.close()
        if admin_bot is not None:
            await admin_bot.session.close()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nЗупинено.")
