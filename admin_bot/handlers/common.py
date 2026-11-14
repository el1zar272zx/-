# admin_bot/handlers/common.py
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.tg import safe_edit

from ..keyboards import admin_reply_kb, main_menu_kb, menu_counts

router = Router(name="admin_common")

MENU_TEXT = "🛠 <b>Адмін-панель</b>\nОберіть розділ:"


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    # Постійна клавіатура внизу + те саме меню кнопками під повідомленням
    await msg.answer(
        "Кнопки керування — внизу екрана 👇", reply_markup=admin_reply_kb(is_super)
    )
    await msg.answer(MENU_TEXT, reply_markup=main_menu_kb(is_super, await menu_counts()))


@router.callback_query(F.data == "adm_menu")
async def back_to_menu(
    call: CallbackQuery, state: FSMContext, is_super: bool = False
) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, MENU_TEXT, main_menu_kb(is_super, await menu_counts()))


@router.callback_query(F.data == "ast_noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()
