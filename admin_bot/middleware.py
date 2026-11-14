# admin_bot/middleware.py
from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from shared.database import session_scope
from shared.services import is_admin, is_superadmin


class AdminOnlyMiddleware(BaseMiddleware):
    """Пускає лише активних адміністраторів; решті показує їхній Telegram ID."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return None

        async with session_scope() as session:
            allowed = await is_admin(session, user.id)
            is_super = allowed and await is_superadmin(session, user.id)

        if not allowed:
            try:
                from shared.alerts import alert_owner

                uname = f"@{user.username}" if user.username else "без username"
                await alert_owner(
                    data.get("bot"),
                    "Хтось стороній відкрив адмін-бота",
                    f"{user.full_name} ({uname}, id {user.id}). Доступу немає, нічого не змінено.",
                )
            except Exception:
                pass
            if isinstance(event, CallbackQuery):
                await event.answer("Немає доступу.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer(
                    "⛔ У вас немає доступу до адмін-панелі.\n\n"
                    f"Ваш Telegram ID: <code>{user.id}</code>\n"
                    "Передайте його власнику бота, щоб він додав вас."
                )
            return None

        if isinstance(event, Message) and event.text:
            from .keyboards import BAR_LABELS

            state = data.get("state")
            if state is not None and event.text in BAR_LABELS:
                await state.clear()  # нижні кнопки завжди працюють, навіть посеред введення
        data["is_super"] = is_super
        return await handler(event, data)


# Які кнопки потрапляють у журнал дій (лише власник його бачить). Префікс → людський опис.
LOGGED = (
    ("abk_ok_", "Підтвердила заявку"),
    ("abk_no_", "Почала відхилення заявки"),
    ("abk_skip_", "Відхилила заявку"),
    ("abk_cancelyes_", "Скасувала запис"),
    ("abk_ns_", "Позначила «не прийшов»"),
    ("abk_paid_", "Змінила позначку оплати"),
    ("abk_undo_", "Скасувала останню дію"),
    ("asv_toggle_", "Показ/приховування послуги"),
    ("asc_excoff_", "Змінила виняток у графіку"),
    ("asc_blkdel_", "Зняла блокування часу"),
    ("ast_adm_del_", "Видалила адміністратора"),
    ("abc_yes", "Надіслала розсилку"),
    ("hol_close_", "Закрила вихідні"),
    ("pro_qbok", "Швидкий запис"),
    ("pro_busy_", "Закрила час («зайнята»)"),
    ("pro_lvl_on", "Перемкнула рівні клієнтів"),
    ("pro_tips_toggle", "Перемкнула поради тижня"),
    ("eh_send", "Розсилка про вільні години"),
)


class ActionLogMiddleware(BaseMiddleware):
    """Після успішної дії записує її в журнал. Помилка журналу ніколи не ламає кнопку."""

    async def __call__(self, handler, event, data):  # type: ignore[no-untyped-def]
        result = await handler(event, data)
        try:
            if isinstance(event, CallbackQuery) and event.data:
                for prefix, label in LOGGED:
                    if event.data.startswith(prefix):
                        from shared import actionlog

                        await actionlog.log(event.from_user.id, label, event.data)
                        break
        except Exception:
            pass
        return result
