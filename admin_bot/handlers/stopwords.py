# admin_bot/handlers/stopwords.py — власні заборонені слова для імен і текстів клієнтів
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared import namefilter
from shared.tg import safe_edit
from shared.utils import esc

from ..keyboards import back, btn, cancel_input_kb, kb
from ..states import StopwordStates

router = Router(name="admin_stopwords")


async def _view():
    words = await namefilter.load_extra()
    text = (
        "🚫 <b>Заборонені слова в іменах</b>\n\n"
        "Бот уже сам не пускає лайку українською, російською й англійською (також латиницею, "
        "з цифрами замість літер і «склеєну» через пробіл). Тут можна додати свої слова: "
        "жартівливі імена або те, що вам не підходить.\n\n"
        + ("Ваш список: " + esc(", ".join(words)) if words else "Свій список порожній.")
    )
    return text, kb(
        [
            [btn("➕ Додати слова", "sw_add")],
            [btn("🗑 Очистити свій список", "sw_clear")],
            [back("sal_menu")],
        ]
    )


@router.callback_query(F.data == "sw_menu")
async def sw_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _view()
    await call.answer()
    await safe_edit(call, text, markup)


@router.callback_query(F.data == "sw_add")
async def sw_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(StopwordStates.text)
    await call.answer()
    await safe_edit(
        call,
        "Напишіть слова через кому (від 3 літер кожне). Наприклад: <code>вася пупкін, test</code>. "
        "Імʼя, у якому трапиться таке слово, бот не прийме.",
        cancel_input_kb("sw_menu"),
    )


@router.message(StopwordStates.text, F.text)
async def sw_save(msg: Message, state: FSMContext) -> None:
    new = [w.strip() for w in msg.text.replace("\n", ",").split(",") if len(w.strip()) >= 3]
    if not new:
        await msg.answer("Не знайшла слів від 3 літер. Спробуйте ще раз.")
        return
    old = await namefilter.load_extra()
    await namefilter.save_extra(old + new)
    await state.clear()
    text, markup = await _view()
    await msg.answer("Додано ✅\n\n" + text, reply_markup=markup)


@router.callback_query(F.data == "sw_clear")
async def sw_clear(call: CallbackQuery) -> None:
    await namefilter.save_extra([])
    await call.answer("Очищено")
    text, markup = await _view()
    await safe_edit(call, text, markup)
