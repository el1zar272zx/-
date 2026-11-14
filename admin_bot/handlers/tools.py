# admin_bot/handlers/tools.py — клієнти, нотатки, звіти, експорт, вихідні, оплата
from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from shared.database import session_scope
from shared.models import Client
from shared.notifications_soft import STATUS_UA
from shared.reports import (
    bookings_csv,
    build_report,
    build_stats,
    clients_csv,
)
from shared.services import (
    close_days,
    count_client_no_shows,
    count_client_visits,
    get_active_cities,
    get_all_clients,
    get_booking,
    get_bookings_in_period,
    get_client_by_id,
    get_client_history,
    search_clients,
    set_client_field,
    toggle_booking_paid,
    update_service,
)
from shared.tg import Event, ack, respond, safe_edit
from shared.utils import esc, fmt_date_long, fmt_date_short, fmt_time, parse_cb_int, today_local

from ..keyboards import (
    A_CLIENTS,
    A_DAYOFF,
    A_WEEK,
    A_REPORT,
    back,
    booking_actions_kb,
    btn,
    cancel_input_kb,
    dates_picker_kb,
    kb,
    menu_only_kb,
)
from ..states import ClientNoteStates, ClientSearchStates, ServiceEditStates
from shared.notifications_soft import format_booking_card

logger = logging.getLogger(__name__)
router = Router(name="admin_tools")


# ---------- Оплата ----------


@router.callback_query(F.data.startswith("abk_paid_"))
async def toggle_paid(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "abk_paid_")
    if booking_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await toggle_booking_paid(session, booking_id)
    if booking is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await call.answer("Позначено оплаченим" if booking.is_paid else "Позначку знято")
    await safe_edit(
        call, format_booking_card(booking, for_admin=True), booking_actions_kb(booking)
    )


# ---------- Клієнти ----------


@router.message(F.text == A_CLIENTS)
async def bar_clients(msg: Message, state: FSMContext) -> None:
    await _clients_start(msg, state)


@router.callback_query(F.data == "acl_start")
async def cb_clients(call: CallbackQuery, state: FSMContext) -> None:
    await _clients_start(call, state)


async def _clients_start(event: Event, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(ClientSearchStates.query)
    await ack(event)
    await respond(
        event,
        "🔎 <b>Пошук клієнта</b>\nНапишіть імʼя, частину номера телефону або #мітку (наприклад, #VIP):",
        cancel_input_kb("adm_menu"),
    )


@router.message(ClientSearchStates.query, F.text)
async def client_query(msg: Message, state: FSMContext) -> None:
    q = (msg.text or "").strip()
    async with session_scope() as session:
        if q.startswith("#") and len(q) > 1:
            tag = q[1:].strip().casefold()
            found = [
                c for c in await get_all_clients(session)
                if tag in [t.strip().casefold() for t in (c.tags or "").split(",")]
            ][:30]
        else:
            found = await search_clients(session, q)
    if not found:
        await msg.answer("Нікого не знайдено. Спробуйте інше імʼя, цифри номера або #мітку.")
        return
    await state.clear()
    rows = [[btn(f"{c.name} · {c.phone}", f"acl_{c.id}")] for c in found]
    rows.append([back("adm_menu")])
    await msg.answer(f"Знайдено: {len(found)}", reply_markup=kb(rows))


def _client_kb(client: Client):
    block = "✅ Дозволити самостійний запис" if client.booking_blocked else "🚫 Вимкнути самостійний запис"
    return kb(
        [
            [btn("📝 Нотатка", f"acn_{client.id}"), btn("🏷 Мітки", f"pro_tag_{client.id}")],
            [btn("🕒 Історія", f"pro_tl_{client.id}"), btn("💌 Порада клієнту", f"pro_adv_{client.id}")],
            [btn("📸 Фото до/після", f"cph_{client.id}")],
            [btn(block, f"acb_{client.id}")],
            [back("adm_menu")],
        ]
    )


async def _client_card(client: Client) -> str:
    async with session_scope() as session:
        visits = await count_client_visits(session, client.telegram_id)
        no_shows = await count_client_no_shows(session, client.telegram_id)
        history = await get_client_history(session, client.telegram_id, 5)
    lines = [
        f"👤 <b>{esc(client.name)}</b>",
        f"Телефон: {esc(client.phone)}",
        f"Візитів: {visits} · Неявок: {no_shows}",
    ]
    if client.birthday:
        lines.append(f"🎂 {client.birthday:%d.%m}")
    if client.contact_pref == "call":
        lines.append("📞 Просить, щоб їй дзвонили")
    if client.booking_blocked:
        lines.append("🚫 Самостійний запис вимкнено")
    if client.tags:
        lines.append(f"🏷 Мітки: {esc(client.tags)}")
    if client.intake:
        lines.append(f"⚕️ Анкета: {esc(client.intake)}")
    if client.about:
        lines.append(f"🙋 Про себе (клієнт): {esc(client.about)}")
    from shared import clientdata as _cd

    lines += await _cd.passport_lines(client.telegram_id)
    lines.append(f"📝 Нотатка: {esc(client.notes)}" if client.notes else "📝 Нотатки немає")
    if history:
        lines += ["", "<b>Останні записи</b>"]
        for b in history:
            lines.append(
                f"• {b.date:%d.%m.%Y} {fmt_time(b.start_time)} · {esc(b.title)} · {STATUS_UA[b.status]}"
            )
            if b.visit_note:
                lines.append(f"   🗒 {esc(b.visit_note)}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("acl_") & ~F.data.startswith("acl_start"))
async def client_show(call: CallbackQuery, state: FSMContext) -> None:
    client_id = parse_cb_int(call.data, "acl_")
    if client_id is None:
        await call.answer()
        return
    await state.clear()
    async with session_scope() as session:
        client = await get_client_by_id(session, client_id)
    if client is None:
        await call.answer("Клієнта не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(
        call,
        await _client_card(client),
        _client_kb(client),
    )


@router.callback_query(F.data.startswith("acn_"))
async def note_start(call: CallbackQuery, state: FSMContext) -> None:
    client_id = parse_cb_int(call.data, "acn_")
    if client_id is None:
        await call.answer()
        return
    await state.set_state(ClientNoteStates.text)
    await state.update_data(client_id=client_id)
    await call.answer()
    await safe_edit(
        call,
        "Напишіть нотатку про клієнта (алергії, побажання тощо).\n"
        "Щоб стерти нотатку, надішліть «-».",
        cancel_input_kb(f"acl_{client_id}"),
    )


@router.message(ClientNoteStates.text, F.text)
async def note_save(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    client_id = data.get("client_id")
    text = msg.text.strip()
    async with session_scope() as session:
        client = await get_client_by_id(session, client_id) if client_id else None
        if client is None:
            await state.clear()
            await msg.answer("Клієнта не знайдено.")
            return
        await set_client_field(
            session, client.telegram_id, notes=None if text == "-" else text[:500]
        )
        client = await get_client_by_id(session, client_id)
    await state.clear()
    await msg.answer(
        "Збережено ✅\n\n" + await _client_card(client),
        reply_markup=_client_kb(client),
    )


# ---------- Звіти та експорт ----------


def _report_menu_kb():
    return kb(
        [
            [btn("🗓 Минулий тиждень", "rep_week"), btn("📆 Цей місяць", "rep_month")],
            [btn("🖼 Розклад на 7 днів (картинка)", "wk_img")],
            [btn("📊 Статистика картинкою", "st_img_30"), btn("📋 Таблиця записів", "tbl_14_0")],
            [btn("📈 Завантаженість (90 днів)", "rep_stats")],
            [btn("📥 Записи в Excel (CSV)", "rep_csv"), btn("👥 Клієнти в Excel", "rep_clients")],
            [back("adm_menu")],
        ]
    )


@router.message(F.text == A_REPORT)
async def bar_report(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("📊 <b>Звіти</b>", reply_markup=_report_menu_kb())


@router.callback_query(F.data == "rep_menu")
async def cb_report(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, "📊 <b>Звіти</b>", _report_menu_kb())


async def _period(first: date, last: date):
    async with session_scope() as session:
        return await get_bookings_in_period(session, first, last)


@router.callback_query(F.data == "rep_week")
async def rep_week(call: CallbackQuery) -> None:
    from shared.reports import week_range

    first, last = week_range(today_local())
    await call.answer()
    await safe_edit(
        call,
        build_report(await _period(first, last), first, last, "Звіт за тиждень"),
        _report_menu_kb(),
    )


@router.callback_query(F.data == "rep_month")
async def rep_month(call: CallbackQuery) -> None:
    today = today_local()
    first = today.replace(day=1)
    await call.answer()
    await safe_edit(
        call,
        build_report(await _period(first, today), first, today, "Звіт за місяць"),
        _report_menu_kb(),
    )


@router.callback_query(F.data == "rep_stats")
async def rep_stats(call: CallbackQuery) -> None:
    today = today_local()
    await call.answer()
    await safe_edit(
        call,
        build_stats(await _period(today - timedelta(days=90), today)),
        _report_menu_kb(),
    )


@router.callback_query(F.data == "rep_csv")
async def rep_csv(call: CallbackQuery) -> None:
    today = today_local()
    rows = await _period(today - timedelta(days=365), today + timedelta(days=60))
    await call.answer()
    await call.message.answer_document(
        BufferedInputFile(bookings_csv(rows), filename=f"zapysy_{today:%Y%m%d}.csv"),
        caption="Записи за рік. Відкрийте файл в Excel.",
    )


@router.callback_query(F.data == "rep_clients")
async def rep_clients(call: CallbackQuery) -> None:
    async with session_scope() as session:
        clients = await get_all_clients(session)
    await call.answer()
    await call.message.answer_document(
        BufferedInputFile(clients_csv(clients), filename=f"klienty_{today_local():%Y%m%d}.csv"),
        caption=f"Клієнтів: {len(clients)}. Відкрийте файл в Excel.",
    )


# ---------- Вихідні / відпустка ----------


@router.message(F.text == A_DAYOFF)
async def bar_dayoff(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(
        "🏖 <b>Вихідні</b>\nОберіть ПЕРШИЙ день вихідних:",
        reply_markup=dates_picker_kb("off1_", "adm_menu", days=60),
    )


@router.callback_query(F.data == "off_start")
async def cb_dayoff(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(
        call,
        "🏖 <b>Вихідні</b>\nОберіть ПЕРШИЙ день вихідних:",
        dates_picker_kb("off1_", "adm_menu", days=60),
    )


@router.callback_query(F.data.startswith("off1_"))
async def off_first(call: CallbackQuery, state: FSMContext) -> None:
    try:
        first = date.fromisoformat(call.data.removeprefix("off1_"))
    except ValueError:
        await call.answer()
        return
    await state.update_data(off_first=first.isoformat())
    await call.answer()
    await safe_edit(
        call,
        f"Перший день: {fmt_date_long(first)}.\nТепер оберіть ОСТАННІЙ день "
        "(для одного дня оберіть ту саму дату):",
        dates_picker_kb("off2_", "off_start", days=60),
    )


@router.callback_query(F.data.startswith("off2_"))
async def off_last(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        first = date.fromisoformat(data["off_first"])
        last = date.fromisoformat(call.data.removeprefix("off2_"))
    except (KeyError, ValueError):
        await call.answer("Почніть спочатку.", show_alert=True)
        return
    if last < first:
        await call.answer("Остання дата раніше за першу.", show_alert=True)
        return
    async with session_scope() as session:
        cities = await get_active_cities(session)
        n, existing = await close_days(session, cities[0].id, first, last)
    await state.clear()
    text = f"✅ Вихідними зроблено днів: {n} ({fmt_date_short(first)} — {fmt_date_short(last)}).\nНові записи на ці дні неможливі."
    if existing:
        text += "\n\n⚠️ На ці дні вже є записи, їх треба скасувати або перенести вручну:\n"
        text += "\n".join(
            f"• {b.date:%d.%m} {fmt_time(b.start_time)} {esc(b.client_name)} ({esc(b.client_phone)})"
            for b in existing[:15]
        )
    await call.answer()
    await safe_edit(call, text, menu_only_kb())


# ---------- Опис послуги ----------


@router.callback_query(F.data.regexp(r"^asv_desc_\d+$"))
async def desc_start(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "asv_desc_")
    if service_id is None:
        await call.answer()
        return
    await state.set_state(ServiceEditStates.description)
    await state.update_data(service_id=service_id)
    await call.answer()
    await safe_edit(
        call,
        "Напишіть опис послуги: що входить, кому підходить, протипоказання. "
        "Його побачать клієнти.\nЩоб стерти опис, надішліть «-».",
        cancel_input_kb(f"asv_svc_{service_id}"),
    )


@router.message(ServiceEditStates.description, F.text)
async def desc_save(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    service_id = data.get("service_id")
    text = msg.text.strip()
    async with session_scope() as session:
        svc = await update_service(
            session,
            service_id,
            description=None if text == "-" else text[:1000],
            set_description=True,
        )
    await state.clear()
    await msg.answer(
        "Опис збережено ✅" if svc else "Послугу не знайдено.", reply_markup=menu_only_kb()
    )


@router.callback_query(F.data.regexp(r"^acb_\d+$"))
async def toggle_block(call: CallbackQuery) -> None:
    client_id = parse_cb_int(call.data, "acb_")
    async with session_scope() as session:
        client = await get_client_by_id(session, client_id or 0)
        if client is None:
            await call.answer("Клієнта не знайдено.", show_alert=True)
            return
        await set_client_field(session, client.telegram_id, booking_blocked=not client.booking_blocked)
        client = await get_client_by_id(session, client.id)
    await call.answer("Запис вимкнено" if client.booking_blocked else "Запис дозволено")
    await safe_edit(call, await _client_card(client), _client_kb(client))


# ---------- Телефон клієнта контактом (натискається й набирається) ----------


@router.callback_query(F.data.regexp(r"^abk_call_\d+$"))
async def send_phone(call: CallbackQuery) -> None:
    booking_id = parse_cb_int(call.data, "abk_call_")
    async with session_scope() as session:
        booking = await get_booking(session, booking_id or 0)
    if booking is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await call.answer()
    await call.message.answer_contact(
        phone_number=booking.client_phone, first_name=booking.client_name[:60] or "Клієнт"
    )


# ---------- Розклад тижня картинкою ----------


@router.message(F.text == A_WEEK)
async def bar_week(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await _send_week(msg)


@router.callback_query(F.data == "wk_img")
async def cb_week(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _send_week(call.message)


async def _send_week(target: Message) -> None:
    import asyncio

    from shared.schedule_image import render_week

    first = today_local()
    async with session_scope() as session:
        bookings = await get_bookings_in_period(session, first, first + timedelta(days=6))
    try:
        png = await asyncio.to_thread(render_week, bookings, first)
    except ImportError:
        await target.answer("Для картинки потрібна бібліотека Pillow. Перезапустіть start.bat, він її встановить.")
        return
    from aiogram.exceptions import TelegramNetworkError

    caption = f"🗓 Розклад на 7 днів. Записів: {sum(1 for b in bookings if b.status.value in ('PENDING', 'CONFIRMED'))}"
    for attempt in (1, 2):
        try:
            await target.answer_photo(
                BufferedInputFile(png, filename="rozklad.jpg"),
                caption=caption,
                request_timeout=90,
            )
            return
        except TelegramNetworkError:
            logger.warning("Картинка розкладу: мережева помилка Telegram, спроба %s", attempt)
    # картинка не доїхала: надсилаємо той самий розклад текстом, щоб не лишати без відповіді
    active = [b for b in bookings if b.status.value in ("PENDING", "CONFIRMED")]
    lines = ["🗓 <b>Розклад на 7 днів</b> (картинка не завантажилась, тому текстом)", ""]
    by_day: dict = {}
    for b in sorted(active, key=lambda x: (x.date, x.start_time)):
        by_day.setdefault(b.date, []).append(b)
    for i in range(7):
        day = first + timedelta(days=i)
        items = by_day.get(day, [])
        lines.append(f"<b>{fmt_date_long(day)}</b>" + ("" if items else " — вільно"))
        for b in items:
            mark = "✅" if b.status.value == "CONFIRMED" else "⏳"
            lines.append(f"{mark} {b.start_time:%H:%M}–{b.end_time:%H:%M} {esc(b.client_name)} · {esc(b.title)}")
    await target.answer("\n".join(lines))
