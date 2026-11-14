# admin_bot/handlers/bookings.py — заявки та записи
from __future__ import annotations

import logging
from datetime import date

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.models import BookingStatus
from shared.notifications_soft import (
    STATUS_UA,
    format_booking_card,
    format_canceled_by_admin_for_client,
    format_confirmed_for_client,
    format_rejected_for_client,
)
from shared.services import (
    get_booking,
    get_bookings_for_date,
    get_pending_bookings,
    set_booking_status,
)
from shared.tg import Event, ack, respond, safe_edit
from shared.waitlist import notify_waitlist
from shared.utils import esc, fmt_date_long, parse_cb_int

from ..keyboards import (
    booking_actions_kb,
    cancel_confirm_kb,
    dates_picker_kb,
    day_bookings_kb,
    menu_only_kb,
    reject_reason_kb,
)
from ..notify import send_to_client
from ..states import RejectStates

logger = logging.getLogger(__name__)
router = Router(name="admin_bookings")

PENDING_PAGE = 10


async def _show_booking(call: CallbackQuery, booking_id: int) -> None:
    async with session_scope() as session:
        booking = await get_booking(session, booking_id)
    if booking is None:
        await safe_edit(call, "Запис не знайдено.", menu_only_kb())
        return
    await safe_edit(
        call, format_booking_card(booking, for_admin=True), booking_actions_kb(booking)
    )


# ---------- Нові заявки ----------


async def show_pending(event: Event, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        pending = await get_pending_bookings(session)
    await ack(event)
    if not pending:
        await respond(event, "Нових заявок немає ✅", menu_only_kb())
        return

    shown = pending[:PENDING_PAGE]
    tail = (
        f"\nПоказано {len(shown)} з {len(pending)}. Опрацюйте їх і відкрийте розділ ще раз."
        if len(pending) > len(shown)
        else ""
    )
    await respond(event, f"📥 Нових заявок: <b>{len(pending)}</b>{tail}", menu_only_kb())
    target = event.message if isinstance(event, CallbackQuery) else event
    for b in shown:
        await target.answer(
            format_booking_card(b, for_admin=True), reply_markup=booking_actions_kb(b)
        )


@router.callback_query(F.data == "abk_new")
async def new_bookings(call: CallbackQuery, state: FSMContext) -> None:
    await show_pending(call, state)


@router.callback_query(F.data.startswith("abk_view_"))
async def view_booking(call: CallbackQuery, state: FSMContext) -> None:
    booking_id = parse_cb_int(call.data, "abk_view_")
    if booking_id is None:
        await call.answer()
        return
    await state.clear()
    await call.answer()
    await _show_booking(call, booking_id)


# ---------- Підтвердження ----------


@router.callback_query(F.data.startswith("abk_ok_"))
async def confirm(call: CallbackQuery, client_bot: Bot | None = None) -> None:
    booking_id = parse_cb_int(call.data, "abk_ok_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking, changed = await set_booking_status(
            session, booking_id, BookingStatus.CONFIRMED
        )
    if booking is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    if not changed:
        await call.answer(f"Статус уже: {STATUS_UA[booking.status]}", show_alert=True)
        await _show_booking(call, booking_id)
        return

    delivered = await send_to_client(
        client_bot, booking, format_confirmed_for_client(booking), with_ics=True
    )
    await call.answer("Підтверджено ✅")
    note = "" if delivered else (
        f"\n\n⚠️ Клієнту не вдалося надіслати повідомлення. "
        f"Зателефонуйте: {esc(booking.client_phone)}"
    )
    await safe_edit(
        call,
        format_booking_card(booking, for_admin=True) + note,
        booking_actions_kb(booking),
    )


# ---------- Відхилення (з причиною) ----------


@router.callback_query(F.data.startswith("abk_no_"))
async def ask_reject_reason(call: CallbackQuery, state: FSMContext) -> None:
    booking_id = parse_cb_int(call.data, "abk_no_")
    if booking_id is None:
        await call.answer()
        return
    await state.set_state(RejectStates.reason)
    await state.update_data(booking_id=booking_id)
    await call.answer()
    await safe_edit(
        call,
        f"Напишіть причину відмови для запису #{booking_id} — її побачить клієнт.\n"
        "Або натисніть «Без причини».",
        reject_reason_kb(booking_id),
    )


async def _do_reject(
    booking_id: int, reason: str | None, client_bot: Bot | None
) -> tuple[str, object | None]:
    async with session_scope() as session:
        booking, changed = await set_booking_status(
            session, booking_id, BookingStatus.REJECTED, reason=reason
        )
    if booking is None:
        return "Запис не знайдено.", None
    if not changed:
        return f"Статус уже: {STATUS_UA[booking.status]}", booking

    delivered = await send_to_client(
        client_bot, booking, format_rejected_for_client(booking)
    )
    await notify_waitlist(client_bot, booking.date)
    text = format_booking_card(booking, for_admin=True)
    if not delivered:
        text += (
            "\n\n⚠️ Клієнту не вдалося надіслати повідомлення. "
            f"Зателефонуйте: {esc(booking.client_phone)}"
        )
    return text, booking


@router.callback_query(F.data.startswith("abk_skip_"))
async def reject_without_reason(
    call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None
) -> None:
    booking_id = parse_cb_int(call.data, "abk_skip_")
    if booking_id is None:
        await call.answer()
        return
    await state.clear()
    text, booking = await _do_reject(booking_id, None, client_bot)
    await call.answer("Відхилено")
    await safe_edit(
        call, text, booking_actions_kb(booking) if booking else menu_only_kb()
    )


@router.message(RejectStates.reason, F.text)
async def reject_with_reason(
    msg: Message, state: FSMContext, client_bot: Bot | None = None
) -> None:
    data = await state.get_data()
    booking_id = data.get("booking_id")
    await state.clear()
    if booking_id is None:
        await msg.answer("Спробуйте ще раз.", reply_markup=menu_only_kb())
        return
    reason = " ".join(msg.text.split())[:300]
    text, booking = await _do_reject(booking_id, reason, client_bot)
    await msg.answer(
        text, reply_markup=booking_actions_kb(booking) if booking else menu_only_kb()
    )


# ---------- Скасування підтвердженого запису ----------


@router.callback_query(F.data.startswith("abk_cancel_"))
async def ask_cancel(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "abk_cancel_")
    if booking_id is None:
        await call.answer()
        return
    await call.answer()
    await safe_edit(
        call,
        f"Скасувати запис #{booking_id}? Клієнт отримає повідомлення.",
        cancel_confirm_kb(booking_id),
    )


@router.callback_query(F.data.startswith("abk_cancelyes_"))
async def do_cancel(call: CallbackQuery, client_bot: Bot | None = None) -> None:
    booking_id = parse_cb_int(call.data, "abk_cancelyes_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking, changed = await set_booking_status(
            session, booking_id, BookingStatus.CANCELED, reason="Скасовано майстром"
        )
    if booking is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    if not changed:
        await call.answer(f"Статус уже: {STATUS_UA[booking.status]}", show_alert=True)
        await _show_booking(call, booking_id)
        return

    delivered = await send_to_client(
        client_bot, booking, format_canceled_by_admin_for_client(booking)
    )
    await notify_waitlist(client_bot, booking.date)
    await call.answer("Скасовано")
    note = "" if delivered else (
        f"\n\n⚠️ Клієнту не вдалося надіслати повідомлення. "
        f"Зателефонуйте: {esc(booking.client_phone)}"
    )
    await safe_edit(
        call,
        format_booking_card(booking, for_admin=True) + note,
        booking_actions_kb(booking),
    )


# ---------- Записи по днях ----------


@router.callback_query(F.data == "abk_days")
async def choose_day(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(
        call, "Оберіть дату:", dates_picker_kb("abk_day_", back_data="adm_menu")
    )


@router.callback_query(F.data.startswith("abk_day_"))
async def day_bookings(call: CallbackQuery) -> None:
    try:
        day = date.fromisoformat(call.data[len("abk_day_"):])
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        bookings = await get_bookings_for_date(session, day)
    await call.answer()
    if not bookings:
        await safe_edit(
            call,
            f"На {fmt_date_long(day)} записів немає.",
            dates_picker_kb("abk_day_", back_data="adm_menu"),
        )
        return
    lines = [f"📅 <b>{fmt_date_long(day)}</b> — записів: {len(bookings)}", ""]
    for b in bookings:
        lines.append(
            f"{b.start_time:%H:%M}–{b.end_time:%H:%M} · {esc(b.city.name)} · "
            f"{esc(b.title)} · {esc(b.client_name)} {STATUS_UA[b.status][:1]}"
        )
    await safe_edit(call, "\n".join(lines), day_bookings_kb(bookings, day))


# ---------- Клієнт не прийшов ----------


@router.callback_query(F.data.startswith("abk_undo_"))
async def undo_action(call: CallbackQuery) -> None:
    from sqlalchemy import select

    from shared import undo
    from shared.models import Booking

    booking_id = parse_cb_int(call.data, "abk_undo_")
    prev = undo.available(booking_id or 0)
    if booking_id is None or prev is None:
        await call.answer("Час для скасування дії минув (15 хвилин).", show_alert=True)
        await _show_booking(call, booking_id or 0)
        return
    async with session_scope() as session:
        booking = await session.get(Booking, booking_id)
        if booking is None:
            await call.answer("Запис не знайдено.", show_alert=True)
            return
        if prev in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
            clash = (
                await session.execute(
                    select(Booking.id).where(
                        Booking.id != booking.id,
                        Booking.date == booking.date,
                        Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
                        Booking.start_time < booking.end_time,
                        Booking.end_time > booking.start_time,
                    )
                )
            ).first()
            if clash:
                await call.answer(
                    "Цей час уже зайнятий іншим записом. Скасувати дію не можна.", show_alert=True
                )
                return
        booking.status = prev
        if prev in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
            booking.cancel_reason = None
    undo.clear(booking_id)
    await call.answer(
        "Повернула. Клієнт уже міг отримати повідомлення, за потреби напишіть йому.",
        show_alert=True,
    )
    await _show_booking(call, booking_id)


@router.callback_query(F.data.startswith("abk_ns_"))
async def mark_no_show(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "abk_ns_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking, changed = await set_booking_status(
            session, booking_id, BookingStatus.NO_SHOW
        )
    if booking is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await call.answer("Позначено: не прийшов" if changed else f"Статус уже: {STATUS_UA[booking.status]}", show_alert=not changed)
    await _show_booking(call, booking_id)
