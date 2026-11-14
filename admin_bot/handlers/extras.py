# admin_bot/handlers/extras.py — нижнє меню, календар (.ics), відгуки, розсилка, бекап
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from shared.calendar_utils import CAL_HOWTO, generate_ics_for_bookings
from shared.database import session_scope
from shared.scheduler import send_backup
from shared.services import (
    get_active_cities,
    get_all_client_ids,
    get_booking,
    get_bookings_between,
    get_bookings_for_date,
    get_categories,
    get_recent_reviews,
    get_review_stats,
)
from shared.tg import Event, ack, respond
from shared.utils import esc, fmt_date_long, today_local

from ..keyboards import (
    A_ADMINS,
    A_BACKUP,
    A_BROADCAST,
    A_CALENDAR,
    A_CARD,
    A_DAYS,
    A_NEW,
    A_REVIEWS,
    A_SCHEDULE,
    A_SERVICES,
    broadcast_confirm_kb,
    calendar_kb,
    cancel_input_kb,
    card_kb,
    categories_admin_kb,
    cities_kb,
    dates_picker_kb,
    menu_only_kb,
)
from ..states import BroadcastStates
from .bookings import show_pending
from .settings import _admins_view, _card_text

logger = logging.getLogger(__name__)
router = Router(name="admin_extras")


def _target(event: Event) -> Message:
    return event.message if isinstance(event, CallbackQuery) else event


# ---------- Кнопки нижнього меню (працюють завжди, скидають поточне введення) ----------


@router.message(F.text == A_NEW)
async def bar_new(msg: Message, state: FSMContext) -> None:
    await show_pending(msg, state)


@router.message(F.text == A_DAYS)
async def bar_days(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("Оберіть дату:", reply_markup=dates_picker_kb("abk_day_", "adm_menu"))


@router.message(F.text == A_SCHEDULE)
async def bar_schedule(msg: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        cities = await get_active_cities(session)
    await msg.answer(
        "🗓 <b>Графік роботи</b>\nОберіть місто:", reply_markup=cities_kb(cities, "asc_city_")
    )


@router.message(F.text == A_SERVICES)
async def bar_services(msg: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        cats = await get_categories(session, include_inactive=True)
    await msg.answer(
        "💰 <b>Послуги та ціни</b>\nОберіть категорію:",
        reply_markup=categories_admin_kb(cats),
    )


@router.message(F.text == A_CARD)
async def bar_card(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(await _card_text(), reply_markup=card_kb())


@router.message(F.text == A_ADMINS)
async def bar_admins(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    if not is_super:
        await msg.answer("Лише для власника.")
        return
    text, kb = await _admins_view()
    await msg.answer(text, reply_markup=kb)


@router.message(F.text == A_REVIEWS)
async def bar_reviews(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await _reviews(msg)


@router.message(F.text == A_CALENDAR)
async def bar_calendar(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(_CAL_TEXT, reply_markup=calendar_kb())


@router.message(F.text == A_BROADCAST)
async def bar_broadcast(msg: Message, state: FSMContext) -> None:
    await _broadcast_start(msg, state)


@router.message(F.text == A_BACKUP)
async def bar_backup(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    await _backup(msg, msg.from_user.id, msg.bot, is_super)


# ---------- Календар (.ics) ----------

_CAL_TEXT = (
    "📆 <b>Додати записи в календар</b>\n"
    "Надішлю файл .ics: відкрийте його на телефоні або комп'ютері, і записи "
    "з'являться в календарі (Google, Apple, Outlook) з нагадуванням за годину.\n"
    "Оберіть період:"
)


async def _send_ics(target: Message, bookings, filename: str, caption: str) -> None:
    if not bookings:
        await target.answer("За цей період записів немає.", reply_markup=menu_only_kb())
        return
    data = generate_ics_for_bookings(bookings, for_admin=True)
    await target.answer_document(
        BufferedInputFile(data, filename=filename),
        caption=f"{caption} ({len(bookings)})\n\n{CAL_HOWTO}",
    )


@router.callback_query(F.data == "abk_cal")
async def calendar_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await respond(call, _CAL_TEXT, calendar_kb())


@router.callback_query(F.data.regexp(r"^abk_icsr_\d+$"))
async def calendar_range(call: CallbackQuery) -> None:
    days = int(call.data.rsplit("_", 1)[1])
    start = today_local()
    end = start + timedelta(days=max(1, days) - 1)
    async with session_scope() as session:
        bookings = await get_bookings_between(session, start, end)
    await call.answer()
    await _send_ics(
        call.message, bookings, f"zapysy_{start:%Y%m%d}_{end:%Y%m%d}.ics", "📆 Записи для календаря"
    )


@router.callback_query(F.data.regexp(r"^abk_ics_\d+$"))
async def calendar_single(call: CallbackQuery) -> None:
    booking_id = int(call.data.rsplit("_", 1)[1])
    async with session_scope() as session:
        booking = await get_booking(session, booking_id)
    if booking is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await call.answer()
    await _send_ics(call.message, [booking], f"zapys_{booking.id}.ics", "📅 Запис для календаря")


@router.callback_query(F.data.startswith("abk_dayics_"))
async def calendar_day(call: CallbackQuery) -> None:
    try:
        day = date.fromisoformat(call.data[len("abk_dayics_"):])
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        bookings = await get_bookings_for_date(session, day)
    await call.answer()
    await _send_ics(
        call.message, bookings, f"zapysy_{day:%Y%m%d}.ics", f"📅 {fmt_date_long(day)}"
    )


# ---------- Відгуки ----------


async def _reviews(event: Event) -> None:
    async with session_scope() as session:
        reviews = await get_recent_reviews(session, 10)
        avg, count = await get_review_stats(session)
    if not reviews:
        await respond(event, "Відгуків поки немає.", menu_only_kb())
        return
    lines = [f"⭐ <b>Відгуки</b>: середня оцінка <b>{avg:.1f}</b> з 5 ({count} шт.)", ""]
    for r in reviews:
        b = r.booking
        line = f"{'⭐' * r.rating} {esc(b.client_name)} — {esc(b.title)}, {b.date:%d.%m}"
        if r.comment:
            line += f"\n   «{esc(r.comment)}»"
        lines.append(line)
    await respond(event, "\n".join(lines), menu_only_kb())


@router.callback_query(F.data == "arv_list")
async def reviews_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _reviews(call)


# ---------- Розсилка ----------


async def _broadcast_start(event: Event, state: FSMContext) -> None:
    from .broadcast import broadcast_start

    await broadcast_start(event, state)


# ---------- Бекап ----------


async def _backup(target: Message, chat_id: int, bot: Bot, is_super: bool) -> None:
    if not is_super:
        await target.answer("Лише для власника.")
        return
    from .backup_ui import backup_menu_text, backup_menu_kb

    await target.answer(backup_menu_text(), reply_markup=backup_menu_kb())


@router.callback_query(F.data == "abc_backup")
async def backup_cb(call: CallbackQuery, is_super: bool = False) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await call.answer()
    await _backup(call.message, call.from_user.id, call.bot, is_super)
