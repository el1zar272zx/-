# admin_bot/notify.py — повідомлення адміністраторам (з клієнтського бота) і клієнтам (з адмінського)
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shared.calendar_utils import CAL_HOWTO, generate_ics_bytes, google_calendar_url
from shared.database import session_scope
from shared.models import Booking
from shared.notifications_soft import (
    format_canceled_by_client_for_admin,
    format_new_booking_for_admin,
    format_rescheduled_for_admin,
    format_review_for_admin,
)
from shared.services import (
    count_client_no_shows,
    get_client,
    count_client_visits,
    get_active_admin_ids,
)
from shared.models import Review
from shared.utils import esc
from sqlalchemy import select

from .keyboards import booking_actions_kb

logger = logging.getLogger(__name__)


async def _send_to_admins(
    admin_bot: Bot | None, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    if admin_bot is None:
        logger.warning(
            "ADMIN_BOT_TOKEN не задано — адміністраторів не повідомлено:\n%s", text
        )
        return
    async with session_scope() as session:
        admin_ids = await get_active_admin_ids(session)
    if not admin_ids:
        logger.warning("Немає активних адміністраторів (вкажіть SUPERADMIN_TELEGRAM_ID в .env)")
        return
    for admin_id in admin_ids:
        try:
            await admin_bot.send_message(admin_id, text, reply_markup=markup)
        except TelegramForbiddenError:
            logger.warning(
                "Адмін %s ще не відкривав адмін-бота: має натиснути /start", admin_id
            )
        except TelegramAPIError:
            logger.exception("Не вдалося надіслати повідомлення адміну %s", admin_id)


async def notify_new_booking(admin_bot: Bot | None, booking: Booking) -> None:
    async with session_scope() as session:
        visits = await count_client_visits(session, booking.client_telegram_id)
        no_shows = await count_client_no_shows(session, booking.client_telegram_id)
        client = await get_client(session, booking.client_telegram_id)
    call_ok = bool(client and client.contact_pref == "call")
    text = format_new_booking_for_admin(
        booking, visits=visits, no_shows=no_shows, call_ok=call_ok
    )
    try:
        from shared.visit_notes import last_note

        extra = []
        if client and client.about:
            extra.append(f"🙋 Про себе: {esc(client.about)}")
        note = await last_note(booking.client_telegram_id, exclude_id=booking.id)
        if note:
            extra.append(f"🗒 Минулого разу: {esc(note)}")
        from shared import clientdata as _cd

        extra.extend(await _cd.passport_lines(booking.client_telegram_id))
        if extra:
            text += "\n\n" + "\n".join(extra)
    except Exception:
        logger.exception("Не вдалося додати нотатки до картки запису")
    await _send_to_admins(admin_bot, text, booking_actions_kb(booking))


async def notify_review(admin_bot: Bot | None, booking_id: int) -> None:
    async with session_scope() as session:
        review = (
            await session.execute(select(Review).where(Review.booking_id == booking_id))
        ).scalars().first()
    if review is None:
        return
    await _send_to_admins(admin_bot, format_review_for_admin(review), None)


async def notify_booking_canceled(admin_bot: Bot | None, booking: Booking) -> None:
    await _send_to_admins(admin_bot, format_canceled_by_client_for_admin(booking), None)


async def notify_booking_rescheduled(
    admin_bot: Bot | None, booking: Booking, old_date, old_start
) -> None:
    await _send_to_admins(
        admin_bot,
        format_rescheduled_for_admin(booking, old_date, old_start),
        booking_actions_kb(booking),
    )


async def notify_client_confirmed(admin_bot: Bot | None, booking: Booking) -> None:
    await _send_to_admins(
        admin_bot,
        f"✅ Клієнт {booking.client_name} підтвердив візит "
        f"{booking.date:%d.%m} о {booking.start_time:%H:%M}.",
        None,
    )


async def send_to_client(
    client_bot: Bot | None, booking: Booking, text: str, *, with_ics: bool = False
) -> bool:
    """Повертає False, якщо клієнту не вдалося доставити (наприклад, заблокував бота)."""
    if client_bot is None:
        return False
    try:
        await client_bot.send_message(booking.client_telegram_id, text)
        if with_ics:
            await client_bot.send_document(
                booking.client_telegram_id,
                BufferedInputFile(
                    generate_ics_bytes(booking), filename=f"zapys_{booking.id}.ics"
                ),
                caption=CAL_HOWTO,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📅 Додати в Google Календар",
                                url=google_calendar_url(booking),
                            )
                        ]
                    ]
                ),
            )
        return True
    except TelegramForbiddenError:
        logger.warning("Клієнт %s заблокував бота", booking.client_telegram_id)
    except TelegramAPIError:
        logger.exception("Не вдалося надіслати повідомлення клієнту %s", booking.client_telegram_id)
    return False


async def notify_followup_issue(admin_bot: Bot | None, booking: Booking) -> None:
    """Клієнт після процедури натиснув «мене щось турбує»."""
    async with session_scope() as session:
        client = await get_client(session, booking.client_telegram_id)
    call = " Клієнт просить дзвонити." if client and client.contact_pref == "call" else ""
    text = (
        "😕 <b>Клієнта щось турбує після процедури</b>\n"
        f"{esc(booking.client_name)}, {esc(booking.client_phone)}\n"
        f"{esc(booking.title)}, {booking.date:%d.%m}.{call}\n"
        "Зв'яжіться з клієнтом."
    )
    await _send_to_admins(admin_bot, text, None)
