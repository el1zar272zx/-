# admin_bot/handlers/settings.py — реквізити картки та адміністратори
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.notifications_soft import format_card_settings_text
from shared.services import (
    add_admin,
    deactivate_admin,
    get_card_settings,
    list_admins,
    update_card_settings,
)
from shared.tg import safe_edit

from ..keyboards import admins_kb, cancel_input_kb, card_kb, menu_only_kb
from ..states import AddAdminStates, CardEditStates

logger = logging.getLogger(__name__)
router = Router(name="admin_settings")

CARD_FIELDS = {
    "card_number": "номер картки",
    "card_holder": "імʼя власника",
    "bank_name": "назву банку",
    "comment": "коментар для клієнта (можна «-», щоб прибрати)",
}


# ---------- Реквізити картки ----------


async def _card_text() -> str:
    async with session_scope() as session:
        cs = await get_card_settings(session)
    return (
        "💳 <b>Реквізити для оплати карткою</b>\n"
        "Саме це бачать клієнти, які обрали оплату карткою:\n\n"
        + format_card_settings_text(cs)
    )


@router.callback_query(F.data == "ast_card")
async def card(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, await _card_text(), card_kb())


@router.callback_query(F.data.startswith("ast_cardedit_"))
async def card_edit_ask(call: CallbackQuery, state: FSMContext) -> None:
    field = call.data[len("ast_cardedit_"):]
    if field not in CARD_FIELDS:
        await call.answer()
        return
    await state.set_state(CardEditStates.value)
    await state.update_data(field=field)
    await call.answer()
    await safe_edit(
        call,
        f"Надішліть {CARD_FIELDS[field]}.",
        cancel_input_kb("ast_card"),
    )


@router.message(CardEditStates.value, F.text)
async def card_edit_save(msg: Message, state: FSMContext) -> None:
    field = (await state.get_data()).get("field")
    if field not in CARD_FIELDS:
        await state.clear()
        await msg.answer("Спробуйте ще раз.", reply_markup=menu_only_kb())
        return
    value = " ".join(msg.text.split())
    if field == "comment" and value in {"-", "—"}:
        value = ""
    if len(value) > 500:
        await msg.answer("Занадто довго (максимум 500 символів).")
        return
    await state.clear()
    async with session_scope() as session:
        await update_card_settings(session, **{field: value})
    await msg.answer("Збережено ✅")
    await msg.answer(await _card_text(), reply_markup=card_kb())


# ---------- Адміністратори (лише суперадмін) ----------


async def _admins_view() -> tuple[str, object]:
    async with session_scope() as session:
        admins = await list_admins(session)
    text = (
        "👥 <b>Адміністратори</b>\n"
        "Вони отримують заявки та керують ботом. 👑 — власник (його не можна прибрати).\n\n"
        "Щоб додати людину, попросіть її написати цьому адмін-боту — "
        "бот покаже її Telegram ID, який потрібно ввести тут."
    )
    return text, admins_kb(admins)


@router.callback_query(F.data == "ast_admins")
async def admins(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, *await _admins_view())


@router.callback_query(F.data == "ast_adm_add")
async def admin_add_ask(
    call: CallbackQuery, state: FSMContext, is_super: bool = False
) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await state.set_state(AddAdminStates.telegram_id)
    await call.answer()
    await safe_edit(
        call,
        "Надішліть Telegram ID нового адміністратора (число).",
        cancel_input_kb("ast_admins"),
    )


@router.message(AddAdminStates.telegram_id, F.text)
async def admin_add_save(
    msg: Message, state: FSMContext, is_super: bool = False
) -> None:
    if not is_super:
        await state.clear()
        return
    raw = msg.text.strip()
    if not raw.isdigit() or not 5 <= len(raw) <= 15:
        await msg.answer("Telegram ID — це число (5–15 цифр). Спробуйте ще раз.")
        return
    await state.clear()
    async with session_scope() as session:
        await add_admin(session, int(raw))
    await msg.answer(
        "Додано ✅ Нехай ця людина відкриє адмін-бота й натисне /start."
    )
    text, markup = await _admins_view()
    await msg.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ast_adm_del_"))
async def admin_remove(
    call: CallbackQuery, state: FSMContext, is_super: bool = False
) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    try:
        telegram_id = int(call.data[len("ast_adm_del_"):])
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        removed = await deactivate_admin(session, telegram_id)
    await call.answer("Прибрано" if removed else "Цього адміністратора прибрати не можна")
    await safe_edit(call, *await _admins_view())
