# admin_bot/handlers/care.py — повторні візити, опитування про самопочуття, стан бота
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from shared import care, health
from shared.database import session_scope
from shared.models import Service
from shared.tg import safe_edit
from shared.utils import esc, parse_cb_int

from ..keyboards import back, btn, cancel_input_kb, kb
from ..states import CareStates

logger = logging.getLogger(__name__)
router = Router(name="admin_care")


def _onoff(v: bool) -> str:
    return "🟢 увімкнено" if v else "⚪️ вимкнено"


def _days(v: int | None) -> str:
    return "вимкнено" if not v else care.interval_label(v)


async def _view() -> tuple[str, object]:
    recall, follow = await care.get_flag("recall_on"), await care.get_flag("followup_on")
    auto, hol = await care.get_flag("autoreply_on"), await care.get_flag("holidays_on")
    async with session_scope() as session:
        services = (
            await session.execute(select(Service).where(Service.is_active.is_(True)).order_by(Service.name))
        ).scalars().all()
    lines = [
        "🔔 <b>Повторні візити й опитування</b>",
        "",
        f"Нагадування про повторний візит: {_onoff(recall)}",
        f"Опитування про самопочуття: {_onoff(follow)}",
        f"Автовідповідь поза робочим часом: {_onoff(auto)}",
        f"Підказки про державні свята: {_onoff(hol)}",
        "",
        "Інтервали вказані для кожної послуги. Для клієнта, що ходить регулярно, "
        "бот бере його власний ритм (від 3 візитів), у межах від половини до півтора інтервалу. "
        "Типові значення (ботокс близько 3,5 міс., губи 6 міс., масаж 2 тиж.) орієнтовні, "
        "підправте їх під свій протокол.",
        "",
    ]
    rows = [
        [btn(f"Повтори: {_onoff(recall)}", "care_t_recall")],
        [btn(f"Опитування: {_onoff(follow)}", "care_t_follow")],
        [btn(f"Автовідповідь: {_onoff(auto)}", "care_t_auto")],
        [btn(f"Свята: {_onoff(hol)}", "care_t_hol")],
    ]
    for s in services[:30]:
        lines.append(f"• {esc(s.name)}: повтор {_days(s.recall_days)}, опитування {('через ' + str(s.followup_days) + ' дн.') if s.followup_days is not None else 'вимкнено'}")
        rows.append([btn(f"⚙️ {s.name[:28]}", f"care_s_{s.id}")])
    rows.append([back("sal_menu")])
    return "\n".join(lines), kb(rows)


@router.callback_query(F.data.regexp(r"^hol_(close|keep)_\d{4}-\d{2}-\d{2}$"))
async def holiday_decide(call: CallbackQuery) -> None:
    from datetime import date

    from shared import holidays
    from shared.services import close_days, get_active_cities

    _, action, iso = call.data.split("_", 2)
    day = date.fromisoformat(iso)
    if action == "keep":
        await holidays.set_decision(day, "kept")
        await call.answer("Залишила робочим")
        await safe_edit(call, f"✅ {day:%d.%m.%Y} лишається робочим днем.")
        return
    existing = 0
    async with session_scope() as session:
        for city in await get_active_cities(session):
            _, bookings = await close_days(session, city.id, day, day)
            existing += len(bookings)
    await holidays.set_decision(day, "closed")
    await call.answer("День закрито")
    text = f"🔒 {day:%d.%m.%Y} закрито для запису."
    if existing:
        text += (
            f"\n⚠️ На цей день уже є записів: {existing}. Вони не скасовані, "
            "перегляньте їх у розділі «Записи» й зв'яжіться з клієнтами."
        )
    await safe_edit(call, text)


@router.callback_query(F.data == "care_menu")
async def care_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    text, markup = await _view()
    await safe_edit(call, text, markup)


@router.callback_query(F.data.in_({"care_t_recall", "care_t_follow", "care_t_auto", "care_t_hol"}))
async def care_toggle(call: CallbackQuery) -> None:
    key = {
        "care_t_recall": "recall_on",
        "care_t_follow": "followup_on",
        "care_t_auto": "autoreply_on",
        "care_t_hol": "holidays_on",
    }[call.data]
    await care.set_flag(key, not await care.get_flag(key))
    await call.answer("Готово")
    text, markup = await _view()
    await safe_edit(call, text, markup)


@router.callback_query(F.data.regexp(r"^care_s_\d+$"))
async def care_service(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "care_s_")
    async with session_scope() as session:
        s = await session.get(Service, sid or 0)
    if s is None:
        await call.answer("Послугу не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(
        call,
        f"<b>{esc(s.name)}</b>\nПовтор: {_days(s.recall_days)}\n"
        f"Опитування про самопочуття: {('через ' + str(s.followup_days) + ' дн.') if s.followup_days is not None else 'вимкнено'}",
        kb(
            [
                [btn("🔁 Змінити інтервал повтору", f"care_r_{s.id}")],
                [btn("💬 Змінити строк опитування", f"care_f_{s.id}")],
                [back("care_menu")],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^care_[rf]_\d+$"))
async def care_ask(call: CallbackQuery, state: FSMContext) -> None:
    kind, sid = call.data.split("_")[1], int(call.data.split("_")[2])
    await state.update_data(service_id=sid)
    await state.set_state(CareStates.recall if kind == "r" else CareStates.followup)
    await call.answer()
    what = (
        "Через скільки ДНІВ після процедури нагадувати про повторний візит? Наприклад: 7, 14, 105. 0 вимикає нагадування."
        if kind == "r"
        else "Через скільки ДНІВ після процедури спитати про самопочуття? Наприклад: 1 або 3. 0 вимикає опитування."
    )
    await safe_edit(call, what, cancel_input_kb(f"care_s_{sid}"))


async def _save(msg: Message, state: FSMContext, field: str) -> None:
    text = (msg.text or "").strip()
    if not text.isdigit() or int(text) > 730:
        await msg.answer("Напишіть число днів від 0 до 730.")
        return
    value = int(text)
    sid = (await state.get_data()).get("service_id")
    async with session_scope() as session:
        s = await session.get(Service, sid or 0)
        if s is None:
            await state.clear()
            await msg.answer("Послугу не знайдено.")
            return
        if field == "recall_days":
            s.recall_days = value or None
        else:
            s.followup_days = value if value > 0 else None
    await state.clear()
    text_, markup = await _view()
    await msg.answer("Збережено ✅\n\n" + text_, reply_markup=markup)


@router.message(CareStates.recall, F.text)
async def save_recall(msg: Message, state: FSMContext) -> None:
    await _save(msg, state, "recall_days")


@router.message(CareStates.followup, F.text)
async def save_followup(msg: Message, state: FSMContext) -> None:
    await _save(msg, state, "followup_days")


@router.message(Command("status"))
async def status(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(await health.status_text())
