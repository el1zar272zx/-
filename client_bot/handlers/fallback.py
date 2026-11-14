# client_bot/handlers/fallback.py — підключається ОСТАННІМ
from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery, Message

from ..keyboards import main_reply_kb

router = Router(name="client_fallback")


@router.message()
async def any_message(msg: Message) -> None:
    await msg.answer("Скористайтеся кнопками меню внизу 👇", reply_markup=main_reply_kb())


@router.callback_query()
async def stale_callback(call: CallbackQuery) -> None:
    await call.answer(
        "Ця кнопка застаріла. Скористайтеся меню внизу.", show_alert=True
    )
