# admin_bot/handlers/schedule.py — графік роботи, винятки, блокування часу
from __future__ import annotations

import logging
from datetime import date, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.models import Booking
from shared.services import (
    add_blocked_slot,
    delete_blocked_slot,
    delete_exception,
    get_active_cities,
    get_bookings_for_date,
    get_city,
    get_day_hours,
    get_exception,
    get_weekly_schedule,
    list_blocked_slots,
    list_exceptions,
    set_weekly_day,
    upsert_exception,
)
from shared.tg import safe_edit
from shared.utils import (
    WEEKDAYS_FULL,
    esc,
    fmt_date_long,
    fmt_hours,
    fmt_time,
    minutes,
    parse_cb_int,
    parse_time_range,
)

from ..keyboards import (
    blocked_list_kb,
    cancel_input_kb,
    cities_kb,
    dates_picker_kb,
    exception_day_kb,
    exceptions_list_kb,
    menu_only_kb,
    schedule_city_kb,
    week_kb,
    weekday_kb,
)
from ..states import BlockTimeStates, ExceptionHoursStates, WeekdayHoursStates

logger = logging.getLogger(__name__)
router = Router(name="admin_schedule")

HOURS_PROMPT = (
    "Надішліть години роботи у форматі <code>10:00-20:00</code> "
    "(початок і кінець через дефіс)."
)


def _parts(data: str, count: int) -> list[str] | None:
    """'asc_wd_1_3' -> ['asc','wd','1','3'] лише якщо частин рівно count."""
    parts = data.split("_")
    return parts if len(parts) == count else None


def _conflict_note(bookings: list[Booking]) -> str:
    if not bookings:
        return ""
    lines = ["", "⚠️ <b>Увага:</b> на цей час уже є записи — звʼяжіться з клієнтами:"]
    for b in bookings:
        lines.append(
            f"• {fmt_time(b.start_time)}–{fmt_time(b.end_time)} "
            f"{esc(b.client_name)}, {esc(b.client_phone)}"
        )
    return "\n".join(lines)


def _outside_hours(bookings: list[Booking], start: time, end: time) -> list[Booking]:
    return [
        b
        for b in bookings
        if minutes(b.start_time) < minutes(start) or minutes(b.end_time) > minutes(end)
    ]


# ---------- Вибір міста ----------


@router.callback_query(F.data == "asc_menu")
async def schedule_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        cities = await get_active_cities(session)
    await call.answer()
    await safe_edit(
        call, "🗓 <b>Графік роботи</b>\nОберіть місто:", cities_kb(cities, "asc_city_")
    )


@router.callback_query(F.data.startswith("asc_city_"))
async def city_menu(call: CallbackQuery, state: FSMContext) -> None:
    city_id = parse_cb_int(call.data, "asc_city_")
    if city_id is None:
        await call.answer()
        return
    await state.clear()
    async with session_scope() as session:
        city = await get_city(session, city_id)
    if city is None:
        await call.answer("Місто не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(
        call,
        f"🗓 <b>{esc(city.name)}</b>\n\n"
        "• <b>Тижневий графік</b> — постійні робочі дні та години.\n"
        "• <b>Виняток</b> — вихідний або інші години на конкретну дату.\n"
        "• <b>Заблокувати час</b> — перерва/справи, діє на обидва міста.",
        schedule_city_kb(city_id),
    )


# ---------- Тижневий графік ----------


async def _show_week(call: CallbackQuery, city_id: int) -> None:
    async with session_scope() as session:
        city = await get_city(session, city_id)
        weekly = await get_weekly_schedule(session, city_id)
    if city is None:
        await safe_edit(call, "Місто не знайдено.", menu_only_kb())
        return
    await safe_edit(
        call,
        f"🗓 <b>{esc(city.name)}</b> — тижневий графік\n"
        "Натисніть на день, щоб змінити години або зробити його вихідним.",
        week_kb(city_id, weekly),
    )


@router.callback_query(F.data.startswith("asc_week_"))
async def week(call: CallbackQuery, state: FSMContext) -> None:
    city_id = parse_cb_int(call.data, "asc_week_")
    if city_id is None:
        await call.answer()
        return
    await state.clear()
    await call.answer()
    await _show_week(call, city_id)


async def _weekday_view(city_id: int, weekday: int) -> tuple[str, object] | None:
    async with session_scope() as session:
        city = await get_city(session, city_id)
        weekly = await get_weekly_schedule(session, city_id)
    if city is None:
        return None
    s = weekly.get(weekday)
    is_off = s is None or s.is_day_off
    current = "вихідний" if is_off else fmt_hours(s.start_time, s.end_time)
    hint = ""
    if is_off and s is not None:
        hint = f"\n(години при поверненні в роботу: {fmt_hours(s.start_time, s.end_time)})"
    text = f"<b>{WEEKDAYS_FULL[weekday]}</b> — {esc(city.name)}\nЗараз: {current}{hint}"
    return text, weekday_kb(city_id, weekday, is_off)


@router.callback_query(F.data.regexp(r"^asc_wd_\d+_[0-6]$"))
async def weekday(call: CallbackQuery, state: FSMContext) -> None:
    parts = _parts(call.data, 4)
    await state.clear()
    view = await _weekday_view(int(parts[2]), int(parts[3]))
    if view is None:
        await call.answer("Місто не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, *view)


@router.callback_query(F.data.regexp(r"^asc_wdtoggle_\d+_[0-6]$"))
async def weekday_toggle(call: CallbackQuery) -> None:
    parts = _parts(call.data, 4)
    city_id, wd = int(parts[2]), int(parts[3])
    async with session_scope() as session:
        weekly = await get_weekly_schedule(session, city_id)
        current = weekly.get(wd)
        make_off = current is None or not current.is_day_off
        await set_weekly_day(session, city_id, wd, is_day_off=make_off)
    await call.answer("Вихідний" if make_off else "Робочий день")
    view = await _weekday_view(city_id, wd)
    if view:
        await safe_edit(call, *view)


@router.callback_query(F.data.regexp(r"^asc_wdhours_\d+_[0-6]$"))
async def weekday_hours_ask(call: CallbackQuery, state: FSMContext) -> None:
    parts = _parts(call.data, 4)
    city_id, wd = int(parts[2]), int(parts[3])
    await state.set_state(WeekdayHoursStates.hours)
    await state.update_data(city_id=city_id, weekday=wd)
    await call.answer()
    await safe_edit(
        call,
        f"<b>{WEEKDAYS_FULL[wd]}</b>\n{HOURS_PROMPT}",
        cancel_input_kb(f"asc_wd_{city_id}_{wd}"),
    )


@router.message(WeekdayHoursStates.hours, F.text)
async def weekday_hours_save(msg: Message, state: FSMContext) -> None:
    parsed = parse_time_range(msg.text)
    if parsed is None or parsed[2]:
        await msg.answer("Не вдалося розпізнати. " + HOURS_PROMPT)
        return
    start, end, _ = parsed
    data = await state.get_data()
    city_id, wd = data["city_id"], data["weekday"]
    await state.clear()
    async with session_scope() as session:
        await set_weekly_day(session, city_id, wd, start=start, end=end, is_day_off=False)
    view = await _weekday_view(city_id, wd)
    await msg.answer("Збережено ✅")
    if view:
        await msg.answer(*view)


# ---------- Винятки на дату ----------


@router.callback_query(F.data.regexp(r"^asc_exc_\d+$"))
async def exception_pick_date(call: CallbackQuery, state: FSMContext) -> None:
    city_id = parse_cb_int(call.data, "asc_exc_")
    await state.clear()
    await call.answer()
    await safe_edit(
        call,
        "Оберіть дату, для якої потрібен виняток:",
        dates_picker_kb(f"asc_excday_{city_id}_", back_data=f"asc_city_{city_id}"),
    )


async def _exception_view(city_id: int, day: date, extra: str = "") -> tuple[str, object] | None:
    async with session_scope() as session:
        city = await get_city(session, city_id)
        hours = await get_day_hours(session, city_id, day)
        exc = await get_exception(session, city_id, day)
    if city is None:
        return None
    current = "вихідний" if hours is None else fmt_hours(*hours)
    source = "виняток" if exc else "за тижневим графіком"
    text = (
        f"📌 <b>{esc(city.name)}</b> — {fmt_date_long(day)}\n"
        f"Зараз: {current} ({source}){extra}"
    )
    return text, exception_day_kb(city_id, day, exc is not None)


def _parse_exc(data: str, prefix: str) -> tuple[int, date] | None:
    parts = _parts(data, 4)
    if not parts or parts[1] != prefix:
        return None
    try:
        return int(parts[2]), date.fromisoformat(parts[3])
    except ValueError:
        return None


@router.callback_query(F.data.startswith("asc_excday_"))
async def exception_day(call: CallbackQuery, state: FSMContext) -> None:
    parsed = _parse_exc(call.data, "excday")
    if parsed is None:
        await call.answer()
        return
    await state.clear()
    view = await _exception_view(*parsed)
    await call.answer()
    if view:
        await safe_edit(call, *view)


@router.callback_query(F.data.startswith("asc_excoff_"))
async def exception_off(call: CallbackQuery) -> None:
    parsed = _parse_exc(call.data, "excoff")
    if parsed is None:
        await call.answer()
        return
    city_id, day = parsed
    async with session_scope() as session:
        await upsert_exception(session, city_id, day, is_day_off=True)
        affected = await get_bookings_for_date(session, day, city_id=city_id)
    await call.answer("Вихідний встановлено")
    view = await _exception_view(city_id, day, _conflict_note(affected))
    if view:
        await safe_edit(call, *view)


@router.callback_query(F.data.startswith("asc_exchours_"))
async def exception_hours_ask(call: CallbackQuery, state: FSMContext) -> None:
    parsed = _parse_exc(call.data, "exchours")
    if parsed is None:
        await call.answer()
        return
    city_id, day = parsed
    await state.set_state(ExceptionHoursStates.hours)
    await state.update_data(city_id=city_id, day=day.isoformat())
    await call.answer()
    await safe_edit(
        call,
        f"{fmt_date_long(day)}\n{HOURS_PROMPT}",
        cancel_input_kb(f"asc_excday_{city_id}_{day.isoformat()}"),
    )


@router.message(ExceptionHoursStates.hours, F.text)
async def exception_hours_save(msg: Message, state: FSMContext) -> None:
    parsed = parse_time_range(msg.text)
    if parsed is None or parsed[2]:
        await msg.answer("Не вдалося розпізнати. " + HOURS_PROMPT)
        return
    start, end, _ = parsed
    data = await state.get_data()
    city_id, day = data["city_id"], date.fromisoformat(data["day"])
    await state.clear()
    async with session_scope() as session:
        await upsert_exception(session, city_id, day, is_day_off=False, start=start, end=end)
        affected = _outside_hours(
            await get_bookings_for_date(session, day, city_id=city_id), start, end
        )
    view = await _exception_view(city_id, day, _conflict_note(affected))
    await msg.answer("Збережено ✅")
    if view:
        await msg.answer(*view)


@router.callback_query(F.data.startswith("asc_excdel_"))
async def exception_delete(call: CallbackQuery) -> None:
    parsed = _parse_exc(call.data, "excdel")
    if parsed is None:
        await call.answer()
        return
    city_id, day = parsed
    async with session_scope() as session:
        removed = await delete_exception(session, city_id, day)
    await call.answer("Виняток прибрано" if removed else "Винятку вже немає")
    view = await _exception_view(city_id, day)
    if view:
        await safe_edit(call, *view)


@router.callback_query(F.data.regexp(r"^asc_list_\d+$"))
async def exceptions_list(call: CallbackQuery) -> None:
    city_id = parse_cb_int(call.data, "asc_list_")
    async with session_scope() as session:
        city = await get_city(session, city_id)
        excs = await list_exceptions(session, city_id)
    await call.answer()
    if city is None:
        await safe_edit(call, "Місто не знайдено.", menu_only_kb())
        return
    if not excs:
        await safe_edit(
            call,
            f"<b>{esc(city.name)}</b>: майбутніх винятків немає.",
            exceptions_list_kb(city_id, []),
        )
        return
    await safe_edit(
        call,
        f"<b>{esc(city.name)}</b> — винятки. Натисніть, щоб прибрати:",
        exceptions_list_kb(city_id, excs),
    )


# ---------- Блокування часу ----------


@router.callback_query(F.data == "asc_blk")
async def block_pick_date(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(
        call,
        "⏸ Оберіть дату, на яку потрібно заблокувати час\n"
        "(діє на обидва міста — майстер один):",
        dates_picker_kb("asc_blkday_", back_data="asc_menu"),
    )


@router.callback_query(F.data.startswith("asc_blkday_"))
async def block_ask_interval(call: CallbackQuery, state: FSMContext) -> None:
    try:
        day = date.fromisoformat(call.data[len("asc_blkday_"):])
    except ValueError:
        await call.answer()
        return
    await state.set_state(BlockTimeStates.interval)
    await state.update_data(day=day.isoformat())
    await call.answer()
    await safe_edit(
        call,
        f"{fmt_date_long(day)}\n"
        "Надішліть проміжок, який треба заблокувати, наприклад:\n"
        "<code>14:00-16:00</code> або <code>14:00-16:00 обід</code>",
        cancel_input_kb("asc_blk"),
    )


@router.message(BlockTimeStates.interval, F.text)
async def block_save(msg: Message, state: FSMContext) -> None:
    parsed = parse_time_range(msg.text)
    if parsed is None:
        await msg.answer(
            "Не вдалося розпізнати. Формат: <code>14:00-16:00</code> "
            "(можна додати коментар через пробіл)."
        )
        return
    start, end, note = parsed
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    await state.clear()
    async with session_scope() as session:
        await add_blocked_slot(session, day, start, end, note[:200] or None)
        overlapping = [
            b
            for b in await get_bookings_for_date(session, day)
            if minutes(b.start_time) < minutes(end) and minutes(b.end_time) > minutes(start)
        ]
    await msg.answer(
        f"⏸ Заблоковано {fmt_date_long(day)} {fmt_hours(start, end)}"
        + (f" ({esc(note)})" if note else "")
        + _conflict_note(overlapping),
        reply_markup=menu_only_kb(),
    )


@router.callback_query(F.data == "asc_blklist")
async def blocked_list(call: CallbackQuery) -> None:
    async with session_scope() as session:
        slots = await list_blocked_slots(session)
    await call.answer()
    if not slots:
        await safe_edit(call, "Заблокованого часу немає.", blocked_list_kb([], "asc_menu"))
        return
    await safe_edit(
        call,
        "⏸ Заблокований час. Натисніть, щоб розблокувати:",
        blocked_list_kb(slots, "asc_menu"),
    )


@router.callback_query(F.data.startswith("asc_blkdel_"))
async def blocked_delete(call: CallbackQuery) -> None:
    slot_id = parse_cb_int(call.data, "asc_blkdel_")
    if slot_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        await delete_blocked_slot(session, slot_id)
        slots = await list_blocked_slots(session)
    await call.answer("Розблоковано")
    if not slots:
        await safe_edit(call, "Заблокованого часу немає.", blocked_list_kb([], "asc_menu"))
        return
    await safe_edit(
        call,
        "⏸ Заблокований час. Натисніть, щоб розблокувати:",
        blocked_list_kb(slots, "asc_menu"),
    )
