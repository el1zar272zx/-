# admin_bot/handlers/services.py — послуги, ціни, категорії
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.models import Service
from shared.services import (
    create_category,
    create_service,
    get_categories,
    get_category,
    get_service,
    get_services_by_category,
    update_service,
)
from shared.tg import safe_edit
from shared.pricing import sale_active
from shared.utils import esc, fmt_price, parse_cb_int

from ..keyboards import (
    cancel_input_kb,
    categories_admin_kb,
    menu_only_kb,
    service_admin_kb,
    services_admin_kb,
)
from ..states import (
    NewCategoryStates,
    NewServiceStates,
    ServiceEditStates,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_services")

PRICE_HINT = "Надішліть ціну в гривнях числом (наприклад <code>450</code>) або «-», щоб прибрати ціну."
DURATION_HINT = "Надішліть тривалість у хвилинах (від 5 до 480), наприклад <code>60</code>."


def _parse_price(text: str) -> tuple[bool, int | None]:
    """(ok, price). '-' / 'немає' -> (True, None)."""
    t = text.strip().lower().replace("грн", "").strip()
    if t in {"-", "—", "немає", "нема", "без ціни"}:
        return True, None
    if t.isdigit() and int(t) <= 1_000_000:
        return True, int(t)
    return False, None


def _parse_duration(text: str) -> int | None:
    t = text.strip().lower().replace("хв", "").strip()
    if t.isdigit() and 5 <= int(t) <= 480:
        return int(t)
    return None


def _service_text(s: Service) -> str:
    state = "показується клієнтам" if s.is_active else "🙈 схована від клієнтів"
    return (
        f"<b>{esc(s.name)}</b>\n"
        f"Категорія: {esc(s.category.name)}\n"
        f"Тривалість: {s.duration_minutes} хв\n"
        f"Ціна: {fmt_price(s.price)}\n"
        + (f"🔥 Акція: {s.sale_price} грн" + (f" до {s.sale_until:%d.%m.%Y}" if s.sale_until else "") + "\n" if sale_active(s) else "")
        + f"Статус: {state}"
    )


async def _show_service(call: CallbackQuery, service_id: int) -> None:
    async with session_scope() as session:
        service = await get_service(session, service_id)
    if service is None:
        await safe_edit(call, "Послугу не знайдено.", menu_only_kb())
        return
    await safe_edit(call, _service_text(service), service_admin_kb(service))


async def _answer_service(msg: Message, service_id: int) -> None:
    async with session_scope() as session:
        service = await get_service(session, service_id)
    if service is None:
        await msg.answer("Послугу не знайдено.", reply_markup=menu_only_kb())
        return
    await msg.answer(_service_text(service), reply_markup=service_admin_kb(service))


# ---------- Перегляд ----------


@router.callback_query(F.data == "asv_menu")
async def categories(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        cats = await get_categories(session, include_inactive=True)
    await call.answer()
    await safe_edit(
        call,
        "💰 <b>Послуги та ціни</b>\nОберіть категорію:",
        categories_admin_kb(cats),
    )


async def _show_category(call: CallbackQuery, category_id: int) -> None:
    async with session_scope() as session:
        cat = await get_category(session, category_id)
        services = await get_services_by_category(
            session, category_id, include_inactive=True
        )
    if cat is None:
        await safe_edit(call, "Категорію не знайдено.", menu_only_kb())
        return
    await safe_edit(
        call,
        f"<b>{esc(cat.name)}</b>\nОберіть послугу, щоб змінити ціну чи тривалість:",
        services_admin_kb(services, category_id),
    )


@router.callback_query(F.data.regexp(r"^asv_cat_\d+$"))
async def category(call: CallbackQuery, state: FSMContext) -> None:
    category_id = parse_cb_int(call.data, "asv_cat_")
    await state.clear()
    await call.answer()
    await _show_category(call, category_id)


@router.callback_query(F.data.regexp(r"^asv_svc_\d+$"))
async def service(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "asv_svc_")
    await state.clear()
    await call.answer()
    await _show_service(call, service_id)


@router.callback_query(F.data.regexp(r"^asv_toggle_\d+$"))
async def toggle(call: CallbackQuery) -> None:
    service_id = parse_cb_int(call.data, "asv_toggle_")
    async with session_scope() as session:
        svc = await get_service(session, service_id)
        if svc is None:
            await call.answer("Послугу не знайдено.", show_alert=True)
            return
        await update_service(session, service_id, is_active=not svc.is_active)
    await call.answer("Готово")
    await _show_service(call, service_id)


# ---------- Редагування ----------


async def _ask(
    call: CallbackQuery,
    state: FSMContext,
    new_state,
    service_id: int,
    prompt: str,
) -> None:
    await state.set_state(new_state)
    await state.update_data(service_id=service_id)
    await call.answer()
    await safe_edit(call, prompt, cancel_input_kb(f"asv_svc_{service_id}"))


@router.callback_query(F.data.regexp(r"^asv_price_\d+$"))
async def ask_price(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "asv_price_")
    await _ask(call, state, ServiceEditStates.price, service_id, PRICE_HINT)


SALE_HINT = (
    "🔥 Акційна ціна. Надішліть нову ціну й, за бажанням, дату закінчення.\n"
    "Приклади: <code>300 до 20.11</code> або просто <code>300</code> (без дати).\n"
    "Щоб прибрати акцію, надішліть <code>-</code>. Стара ціна покажеться закресленою."
)


@router.callback_query(F.data.regexp(r"^asv_sale_\d+$"))
async def ask_sale(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "asv_sale_")
    await _ask(call, state, ServiceEditStates.sale, service_id, SALE_HINT)


@router.message(ServiceEditStates.sale, F.text)
async def save_sale(msg: Message, state: FSMContext) -> None:
    import re
    from datetime import date

    from shared.utils import today_local

    text = msg.text.strip().lower()
    service_id = (await state.get_data())["service_id"]
    price: int | None = None
    until = None
    if text not in ("-", "немає", "нема", "0"):
        m = re.match(r"^(\d{1,6})(?:\s*(?:до)?\s*(\d{1,2})[.](\d{1,2})(?:[.](\d{2,4}))?)?$", text)
        if not m:
            await msg.answer("Не вдалося розпізнати. " + SALE_HINT)
            return
        price = int(m.group(1))
        if m.group(2):
            today = today_local()
            year = int(m.group(4)) if m.group(4) else today.year
            if year < 100:
                year += 2000
            try:
                until = date(year, int(m.group(3)), int(m.group(2)))
            except ValueError:
                await msg.answer("Така дата не існує. " + SALE_HINT)
                return
            if not m.group(4) and until < today:
                until = until.replace(year=year + 1)
    async with session_scope() as session:
        svc = await get_service(session, service_id)
        if svc is None:
            await state.clear()
            await msg.answer("Послугу не знайдено.")
            return
        if price is not None and svc.price is not None and price >= svc.price:
            await msg.answer("Акційна ціна має бути меншою за звичайну (%s грн)." % svc.price)
            return
        svc.sale_price = price
        svc.sale_until = until
    await state.clear()
    await msg.answer("Готово ✅ Акцію прибрано." if price is None else "Готово ✅ Акцію збережено.")
    await _answer_service(msg, service_id)


@router.callback_query(F.data.regexp(r"^asv_dur_\d+$"))
async def ask_duration(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "asv_dur_")
    await _ask(call, state, ServiceEditStates.duration, service_id, DURATION_HINT)


@router.callback_query(F.data.regexp(r"^asv_name_\d+$"))
async def ask_name(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "asv_name_")
    await _ask(call, state, ServiceEditStates.name, service_id, "Надішліть нову назву послуги.")


@router.message(ServiceEditStates.price, F.text)
async def save_price(msg: Message, state: FSMContext) -> None:
    ok, price = _parse_price(msg.text)
    if not ok:
        await msg.answer("Не вдалося розпізнати. " + PRICE_HINT)
        return
    service_id = (await state.get_data())["service_id"]
    await state.clear()
    async with session_scope() as session:
        await update_service(session, service_id, price=price, set_price=True)
    await msg.answer("Ціну збережено ✅")
    await _answer_service(msg, service_id)


@router.message(ServiceEditStates.duration, F.text)
async def save_duration(msg: Message, state: FSMContext) -> None:
    duration = _parse_duration(msg.text)
    if duration is None:
        await msg.answer("Не вдалося розпізнати. " + DURATION_HINT)
        return
    service_id = (await state.get_data())["service_id"]
    await state.clear()
    async with session_scope() as session:
        await update_service(session, service_id, duration_minutes=duration)
    await msg.answer("Тривалість збережено ✅")
    await _answer_service(msg, service_id)


@router.message(ServiceEditStates.name, F.text)
async def save_name(msg: Message, state: FSMContext) -> None:
    name = " ".join(msg.text.split())
    if not 2 <= len(name) <= 150:
        await msg.answer("Назва має містити від 2 до 150 символів.")
        return
    service_id = (await state.get_data())["service_id"]
    await state.clear()
    async with session_scope() as session:
        await update_service(session, service_id, name=name)
    await msg.answer("Назву збережено ✅")
    await _answer_service(msg, service_id)


# ---------- Нова категорія ----------


@router.callback_query(F.data == "asv_newcat")
async def new_category_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(NewCategoryStates.name)
    await call.answer()
    await safe_edit(call, "Надішліть назву нової категорії.", cancel_input_kb("asv_menu"))


@router.message(NewCategoryStates.name, F.text)
async def new_category_save(msg: Message, state: FSMContext) -> None:
    name = " ".join(msg.text.split())
    if not 2 <= len(name) <= 100:
        await msg.answer("Назва має містити від 2 до 100 символів.")
        return
    try:
        async with session_scope() as session:
            cat = await create_category(session, name)
    except ValueError as e:
        await msg.answer(str(e))
        return
    await state.clear()
    await msg.answer(
        f"Категорію «{esc(cat.name)}» створено ✅", reply_markup=menu_only_kb()
    )


# ---------- Нова послуга (покроково) ----------


@router.callback_query(F.data.regexp(r"^asv_new_\d+$"))
async def new_service_ask_name(call: CallbackQuery, state: FSMContext) -> None:
    category_id = parse_cb_int(call.data, "asv_new_")
    await state.set_state(NewServiceStates.name)
    await state.update_data(category_id=category_id)
    await call.answer()
    await safe_edit(
        call, "Надішліть назву нової послуги.", cancel_input_kb(f"asv_cat_{category_id}")
    )


@router.message(NewServiceStates.name, F.text)
async def new_service_name(msg: Message, state: FSMContext) -> None:
    name = " ".join(msg.text.split())
    if not 2 <= len(name) <= 150:
        await msg.answer("Назва має містити від 2 до 150 символів.")
        return
    await state.update_data(name=name)
    await state.set_state(NewServiceStates.duration)
    await msg.answer(DURATION_HINT)


@router.message(NewServiceStates.duration, F.text)
async def new_service_duration(msg: Message, state: FSMContext) -> None:
    duration = _parse_duration(msg.text)
    if duration is None:
        await msg.answer("Не вдалося розпізнати. " + DURATION_HINT)
        return
    await state.update_data(duration=duration)
    await state.set_state(NewServiceStates.price)
    await msg.answer(PRICE_HINT)


@router.message(NewServiceStates.price, F.text)
async def new_service_price(msg: Message, state: FSMContext) -> None:
    ok, price = _parse_price(msg.text)
    if not ok:
        await msg.answer("Не вдалося розпізнати. " + PRICE_HINT)
        return
    data = await state.get_data()
    try:
        async with session_scope() as session:
            svc = await create_service(
                session,
                category_id=data["category_id"],
                name=data["name"],
                duration_minutes=data["duration"],
                price=price,
            )
            service_id = svc.id
    except ValueError as e:
        await state.clear()
        await msg.answer(str(e), reply_markup=menu_only_kb())
        return
    await state.clear()
    await msg.answer("Послугу створено ✅")
    await _answer_service(msg, service_id)
