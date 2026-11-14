# client_bot/handlers/my_bookings.py
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from datetime import date, time

from admin_bot.notify import (
    notify_booking_canceled,
    notify_booking_rescheduled,
    notify_client_confirmed,
)
from shared.database import session_scope
from shared.notifications_soft import format_booking_card
from shared.services import (
    BookingError,
    can_client_change,
    cancel_booking_by_client,
    get_available_time_slots,
    get_booking,
    get_client_bookings,
    get_working_dates,
    mark_client_confirmed,
    reschedule_booking_atomic,
)
from shared.tg import Event, ack, respond, safe_edit
from shared.waitlist import notify_waitlist
from shared.utils import fmt_date_long, fmt_time, minutes, parse_cb_int

from ..keyboards import (
    BTN_MY,
    T,
    booking_cancel_confirm_kb,
    booking_manage_kb,
    move_dates_kb,
    move_times_kb,
    my_bookings_kb,
)

logger = logging.getLogger(__name__)
router = Router(name="client_my_bookings")


async def _show_list(event: Event, state: FSMContext) -> None:
    async with session_scope() as session:
        bookings = await get_client_bookings(session, event.from_user.id)
    await state.clear()
    if not bookings:
        from ..keyboards import repeat_kb

        await respond(event, "У вас немає майбутніх записів.", repeat_kb())
        return
    await respond(event, "Ваші майбутні записи:", my_bookings_kb(bookings))


@router.message(F.text.in_(T(BTN_MY)))
async def menu_my_bookings(msg: Message, state: FSMContext) -> None:
    await _show_list(msg, state)


@router.callback_query(F.data == "back_my_bookings")
async def back_to_my_bookings(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await _show_list(call, state)


@router.callback_query(F.data.startswith("myb_"))
async def view_single_booking(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "myb_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await get_booking(session, booking_id)
    if not booking or booking.client_telegram_id != call.from_user.id:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await call.answer()
    from shared import salon
    from shared.models import PaymentMethod

    pay = salon.info("pay_url") if booking.payment_method == PaymentMethod.CARD and not booking.is_paid else ""
    from shared.booking_steps import steps_text

    await safe_edit(call, format_booking_card(booking) + "\n\n" + steps_text(booking), booking_manage_kb(booking.id, pay))


@router.callback_query(F.data.startswith("mybcancel_"))
async def ask_cancel(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "mybcancel_")
    if booking_id is None:
        await call.answer()
        return
    await call.answer()
    await safe_edit(
        call,
        f"Скасувати запис #{booking_id}?",
        booking_cancel_confirm_kb(booking_id),
    )


@router.callback_query(F.data.startswith("mybcancelyes_"))
async def do_cancel(
    call: CallbackQuery, state: FSMContext, admin_bot: Bot | None = None
) -> None:
    booking_id = parse_cb_int(call.data, "mybcancelyes_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await cancel_booking_by_client(session, booking_id, call.from_user.id)
    if booking is None:
        await call.answer("Не вдалося скасувати цей запис.", show_alert=True)
        await _show_list(call, state)
        return

    await call.answer("Запис скасовано.")
    await _show_list(call, state)
    try:
        await notify_booking_canceled(admin_bot, booking)
    except Exception:
        logger.exception("Не вдалося повідомити адміністраторів про скасування #%s", booking_id)
    await notify_waitlist(call.bot, booking.date)


# ---------- Підтвердження візиту з нагадування ----------


@router.callback_query(F.data.startswith("remok_"))
async def reminder_ok(call: CallbackQuery, admin_bot: Bot | None = None) -> None:
    booking_id = parse_cb_int(call.data, "remok_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await mark_client_confirmed(session, booking_id, call.from_user.id)
    if booking is None:
        await call.answer("Запис уже неактивний.", show_alert=True)
        return
    await call.answer("Дякуємо!")
    await safe_edit(
        call,
        "✅ Дякуємо, що підтвердили! Чекаємо на вас.\n\n" + format_booking_card(booking),
    )
    try:
        await notify_client_confirmed(admin_bot, booking)
    except Exception:
        logger.exception("Не вдалося повідомити адміна про підтвердження #%s", booking_id)


# ---------- Перенесення запису ----------


@router.callback_query(F.data.startswith("mybmove_"))
async def move_start(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "mybmove_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await get_booking(session, booking_id)
        if booking is None or booking.client_telegram_id != call.from_user.id:
            await call.answer("Запис не знайдено.", show_alert=True)
            return
        if not can_client_change(booking):
            await call.answer(
                "Перенести вже пізно. Будь ласка, зателефонуйте майстру.", show_alert=True
            )
            return
        dates = await get_working_dates(session, booking.city_id)
    if not dates:
        await call.answer("Наразі немає вільних днів.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, "Оберіть нову дату:", move_dates_kb(booking_id, dates))


@router.callback_query(F.data.startswith("mvd_"))
async def move_pick_date(call: CallbackQuery) -> None:
    try:
        _, bid, iso = call.data.split("_", 2)
        booking_id, day = int(bid), date.fromisoformat(iso)
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await get_booking(session, booking_id)
        if booking is None or booking.client_telegram_id != call.from_user.id:
            await call.answer("Запис не знайдено.", show_alert=True)
            return
        duration = minutes(booking.end_time) - minutes(booking.start_time)
        slots = await get_available_time_slots(
            session,
            city_id=booking.city_id,
            for_date=day,
            duration_minutes=duration,
            exclude_booking_id=booking.id,
        )
    if not slots:
        await call.answer(f"На {fmt_date_long(day)} вільних годин немає.", show_alert=True)
        return
    await call.answer()
    await safe_edit(
        call,
        f"Оберіть новий час на {fmt_date_long(day)}:",
        move_times_kb(booking_id, day, [fmt_time(t) for t in slots]),
    )


@router.callback_query(F.data.startswith("mvt_"))
async def move_confirm(call: CallbackQuery, admin_bot: Bot | None = None) -> None:
    try:
        _, bid, iso, hhmm = call.data.split("_", 3)
        booking_id, day = int(bid), date.fromisoformat(iso)
        new_start = time(int(hhmm[:2]), int(hhmm[2:]))
    except ValueError:
        await call.answer()
        return
    try:
        booking, old_date, old_start = await reschedule_booking_atomic(
            booking_id, call.from_user.id, day, new_start
        )
    except BookingError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await call.answer("Запис перенесено.")
    await safe_edit(
        call, "🔄 Запис перенесено.\n\n" + format_booking_card(booking), booking_manage_kb(booking.id)
    )
    try:
        await notify_booking_rescheduled(admin_bot, booking, old_date, old_start)
    except Exception:
        logger.exception("Не вдалося повідомити адміна про перенесення #%s", booking_id)
    await notify_waitlist(call.bot, old_date)


@router.callback_query(F.data.startswith("mybnext_"))
async def book_next_week(call: CallbackQuery, admin_bot: Bot | None = None) -> None:
    """Та сама послуга, той самий час, через 7 днів (заявка очікує підтвердження)."""
    from datetime import timedelta

    from admin_bot.notify import notify_new_booking
    from shared.services import create_booking_atomic

    booking_id = parse_cb_int(call.data, "mybnext_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        old = await get_booking(session, booking_id)
    if old is None or old.client_telegram_id != call.from_user.id:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    try:
        new = await create_booking_atomic(
            city_id=old.city_id,
            service_ids=[old.service_id],
            client_telegram_id=old.client_telegram_id,
            client_name=old.client_name,
            client_phone=old.client_phone,
            booking_date=old.date + timedelta(days=7),
            start_time=old.start_time,
            payment_method=old.payment_method,
        )
    except BookingError as e:
        await call.answer(f"На цей час записатися не вдалося: {e}", show_alert=True)
        return
    except Exception:
        logger.exception("Не вдалося створити повторний запис")
        await call.answer("Сталася помилка. Спробуйте пізніше.", show_alert=True)
        return
    await call.answer("Заявку створено ✅")
    await safe_edit(
        call,
        f"🔁 Заявку створено: {fmt_date_long(new.date)}, {fmt_time(new.start_time)}.\n"
        "Майстер підтвердить її найближчим часом.",
    )
    try:
        await notify_new_booking(admin_bot, new)
    except Exception:
        logger.exception("Не вдалося повідомити адміністраторів про запис #%s", new.id)
