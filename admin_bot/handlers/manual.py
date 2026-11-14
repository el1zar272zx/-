# admin_bot/handlers/manual.py — адмін записує клієнта вручну (наприклад, після дзвінка)
from __future__ import annotations

import logging
from datetime import date, time

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.models import BookingStatus, PaymentMethod
from shared.notifications_soft import format_booking_card
from shared.services import (
    BookingError,
    create_booking_atomic,
    get_active_cities,
    get_available_time_slots,
    get_categories,
    get_client_by_phone,
    get_service,
    get_services_by_category,
    phone_pseudo_id,
)
from shared.tg import Event, ack, respond, safe_edit
from shared.utils import chunked, esc, fmt_date_long, fmt_time, normalize_phone, parse_cb_int

from ..notify import send_to_client
from ..keyboards import A_MANUAL, back, btn, booking_actions_kb, cancel_input_kb, dates_picker_kb, kb
from ..states import ManualBookingStates

logger = logging.getLogger(__name__)
router = Router(name="admin_manual")

CANCEL = "adm_menu"


@router.message(F.text == A_MANUAL)
async def bar_manual(msg: Message, state: FSMContext) -> None:
    await _start(msg, state)


@router.callback_query(F.data == "man_start")
async def cb_manual(call: CallbackQuery, state: FSMContext) -> None:
    await _start(call, state)


async def _start(event: Event, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ManualBookingStates.name)
    await ack(event)
    await respond(
        event,
        "➕ <b>Запис клієнта вручну</b>\nНапишіть ім'я клієнта:",
        cancel_input_kb(CANCEL),
    )


@router.message(ManualBookingStates.name, F.text)
async def got_name(msg: Message, state: FSMContext) -> None:
    name = msg.text.strip()
    if not 2 <= len(name) <= 100:
        await msg.answer("Ім'я має містити від 2 до 100 символів.")
        return
    await state.update_data(name=name)
    await state.set_state(ManualBookingStates.phone)
    await msg.answer("Тепер номер телефону клієнта:", reply_markup=cancel_input_kb(CANCEL))


@router.message(ManualBookingStates.phone, F.text)
async def got_phone(msg: Message, state: FSMContext) -> None:
    phone = normalize_phone(msg.text)
    if phone is None:
        await msg.answer("Не схоже на номер. Приклад: +380501234567")
        return
    await state.update_data(phone=phone)
    await state.set_state(None)
    async with session_scope() as session:
        cats = await get_categories(session)
    rows = [[btn(c.name, f"man_cat_{c.id}")] for c in cats]
    rows.append([back(CANCEL)])
    await msg.answer("Оберіть категорію послуги:", reply_markup=kb(rows))


@router.callback_query(F.data.startswith("man_cat_"))
async def pick_category(call: CallbackQuery) -> None:
    cat_id = parse_cb_int(call.data, "man_cat_")
    if cat_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        services = await get_services_by_category(session, cat_id)
    rows = [[btn(f"{s.name} · {s.duration_minutes} хв", f"man_svc_{s.id}")] for s in services]
    rows.append([back(CANCEL)])
    await call.answer()
    await safe_edit(call, "Оберіть послугу:", kb(rows))


@router.callback_query(F.data.startswith("man_svc_"))
async def pick_service(call: CallbackQuery, state: FSMContext) -> None:
    svc_id = parse_cb_int(call.data, "man_svc_")
    data = await state.get_data()
    if svc_id is None or "phone" not in data:
        await call.answer("Почніть спочатку: ➕ Записати клієнта", show_alert=True)
        return
    await state.update_data(service_id=svc_id)
    await call.answer()
    await safe_edit(call, "Оберіть дату:", dates_picker_kb("man_date_", CANCEL))


@router.callback_query(F.data.startswith("man_date_"))
async def pick_date(call: CallbackQuery, state: FSMContext) -> None:
    try:
        day = date.fromisoformat(call.data.removeprefix("man_date_"))
    except ValueError:
        await call.answer()
        return
    data = await state.get_data()
    if "service_id" not in data:
        await call.answer("Почніть спочатку: ➕ Записати клієнта", show_alert=True)
        return
    async with session_scope() as session:
        svc = await get_service(session, data["service_id"])
        cities = await get_active_cities(session)
        if svc is None or not cities:
            await call.answer("Послугу або місто не знайдено.", show_alert=True)
            return
        slots = await get_available_time_slots(
            session, city_id=cities[0].id, for_date=day, service=svc
        )
    if not slots:
        await call.answer(f"На {fmt_date_long(day)} вільних годин немає.", show_alert=True)
        return
    await state.update_data(date=day.isoformat())
    rows = [
        [btn(fmt_time(t), f"man_time_{t.strftime('%H%M')}") for t in chunk]
        for chunk in chunked(slots, 4)
    ]
    rows.append([back(CANCEL)])
    await call.answer()
    await safe_edit(call, f"Оберіть час на {fmt_date_long(day)}:", kb(rows))


@router.callback_query(F.data.startswith("man_time_"))
async def pick_time(
    call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None
) -> None:
    hhmm = call.data.removeprefix("man_time_")
    data = await state.get_data()
    try:
        start = time(int(hhmm[:2]), int(hhmm[2:]))
        day = date.fromisoformat(data["date"])
    except (ValueError, KeyError):
        await call.answer("Почніть спочатку: ➕ Записати клієнта", show_alert=True)
        return
    async with session_scope() as session:
        cities = await get_active_cities(session)
        registered = await get_client_by_phone(session, data["phone"])
    # Якщо клієнт уже користувався ботом, записуємо на його Telegram: тоді він отримає
    # повідомлення, нагадування й зможе переносити запис сам.
    tg_id = registered.telegram_id if registered else phone_pseudo_id(data["phone"])
    try:
        booking = await create_booking_atomic(
            city_id=cities[0].id,
            service_ids=[data["service_id"]],
            client_telegram_id=tg_id,
            client_name=data["name"],
            client_phone=data["phone"],
            booking_date=day,
            start_time=start,
            payment_method=PaymentMethod.CASH,
            status=BookingStatus.CONFIRMED,
        )
    except BookingError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await call.answer("Запис створено")
    if registered:
        sent = await send_to_client(
            client_bot,
            booking,
            "✅ <b>Майстер записала вас на прийом.</b>\n\n" + format_booking_card(booking),
            with_ics=True,
        )
        note = (
            "📨 Клієнту надіслано повідомлення про запис."
            if sent
            else "⚠️ Повідомлення клієнту не доставлено (можливо, він заблокував бота). Повідомте його самі."
        )
    else:
        note = (
            "ℹ️ Цей клієнт ще не користується ботом, тож повідомлення й нагадування "
            "йому не надсилаються. Попросіть його відкрити бота й натиснути /start: "
            "наступного разу запис підтягнеться автоматично."
        )
    await safe_edit(
        call,
        "✅ Клієнта записано.\n\n"
        + format_booking_card(booking, for_admin=True)
        + "\n\n"
        + note,
        booking_actions_kb(booking),
    )
