# client_bot/handlers/profile.py — «Мій профіль»
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.config import settings
from shared.database import session_scope
from shared.services import count_client_visits, get_client, save_client, set_client_field
from shared.tg import safe_edit
from shared.utils import esc

from ..keyboards import (
    T,
    BTN_BOOK,
    BTN_MY,
    BTN_PROFILE,
    BTN_SERVICES,
    back_only_kb,
    main_reply_kb,
    profile_kb,
)
from ..states import ProfileStates

import logging

logger = logging.getLogger(__name__)
router = Router(name="client_profile")

MENU_BUTTONS = set().union(*(T(b) for b in (BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_SERVICES)))


async def _profile_text(tg_id: int) -> str | None:
    async with session_scope() as session:
        client = await get_client(session, tg_id)
        visits = await count_client_visits(session, tg_id)
    if client is None:
        return None
    text = (
        "<b>👤 Мій профіль</b>\n\n"
        f"Імʼя: {esc(client.name)}\n"
        f"Телефон: {esc(client.phone)}\n"
        f"Завершених візитів: {visits}"
    )
    n, pct = settings.loyalty_every, settings.loyalty_discount_percent
    if n > 0 and pct > 0:
        left = n - (visits % n)
        text += (
            f"\n\n🎁 Кожен {n}-й візит зі знижкою {pct}%. "
            + ("Наступний візит — зі знижкою!" if left == 1 else f"До знижки: {left - 1} візитів.")
        )
    try:
        from shared import levels

        lvl = levels.progress_text(visits)
        if lvl:
            text += "\n\n" + lvl
    except Exception:
        logger.exception("Рівень клієнта не показано")
    text += "\nЗв'язок: " + ("телефонний дзвінок" if client.contact_pref == "call" else "Telegram")
    if client.about:
        text += f"\n📝 Про мене: {esc(client.about)}"
    if client.quiet:
        from shared.quiet import label

        text += "\n🌙 " + label(client.quiet)
    try:
        from shared import clientdata as cd
        from shared import selfcare_data as sd

        comfort = await cd.get_json(tg_id, "comfort")
        if comfort:
            parts = [sd.comfort_label(k, comfort[k]) for k, _q, _o in sd.COMFORT if k in comfort]
            text += "\n🎛 Вподобання сеансу: " + ", ".join(parts)
    except Exception:
        logger.exception("Вподобання сеансу не показано")
    text += "\n\nЩоб змінити номер, натисніть «📱 Оновити номер» внизу."
    return text


@router.message(F.text.in_(T(BTN_PROFILE)))
async def show_profile(msg: Message, state: FSMContext) -> None:
    await state.clear()
    text = await _profile_text(msg.from_user.id)
    if text is None:
        return
    await msg.answer(text, reply_markup=profile_kb())


@router.callback_query(F.data == "profile_name")
async def ask_name(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileStates.EDIT_NAME)
    await call.answer()
    await safe_edit(call, "Напишіть, як до вас звертатися (імʼя):", back_only_kb("profile_back"))


async def _flag_abuse(msg, admin_bot) -> None:
    """Після 3 грубих спроб тихо повідомляємо майстра."""
    from shared import namefilter

    if namefilter.strike(msg.from_user.id) != 3 or admin_bot is None:
        return
    try:
        from shared.services import get_active_admin_ids

        async with session_scope() as session:
            ids = await get_active_admin_ids(session)
        uname = f"@{msg.from_user.username}" if msg.from_user.username else "без username"
        for admin_id in ids:
            await admin_bot.send_message(
                admin_id,
                f"🚩 Користувач {esc(msg.from_user.full_name)} ({uname}, id {msg.from_user.id}) "
                "тричі ввів грубі слова в імені чи тексті. Нічого не заблоковано; "
                "за потреби вимкніть самостійний запис у картці клієнта.",
            )
    except Exception:
        logger.exception("Не вдалося повідомити про грубі слова")


@router.callback_query(F.data == "profile_about")
async def ask_about(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileStates.EDIT_ABOUT)
    await call.answer()
    await safe_edit(
        call,
        "📝 Напишіть коротко про себе для майстра: тип шкіри, де болить чи є напруга, "
        "що важливо врахувати. Це побачить лише майстер. Щоб стерти, надішліть «-».",
        back_only_kb("profile_back"),
    )


@router.message(ProfileStates.EDIT_ABOUT, F.text)
async def save_about(msg: Message, state: FSMContext, admin_bot=None) -> None:
    text = " ".join(msg.text.split())
    if text.startswith("/") or text in MENU_BUTTONS:
        await msg.answer("Напишіть коротко про себе або «-», щоб стерти.")
        return
    from shared import namefilter

    if namefilter.has_obscene(text):
        await _flag_abuse(msg, admin_bot)
        await msg.answer(namefilter.TEXT_REJECT)
        return
    async with session_scope() as session:
        await set_client_field(session, msg.from_user.id, about=None if text == "-" else text[:300])
    await state.clear()
    await msg.answer("Збережено ✅")
    shown = await _profile_text(msg.from_user.id)
    if shown:
        await msg.answer(shown, reply_markup=profile_kb())


@router.callback_query(F.data == "profile_back")
async def profile_back(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    text = await _profile_text(call.from_user.id)
    if text:
        await safe_edit(call, text, profile_kb())


@router.message(ProfileStates.EDIT_NAME, F.text)
async def save_name(msg: Message, state: FSMContext, admin_bot=None) -> None:
    name = " ".join(msg.text.split())
    if name.startswith("/") or name in MENU_BUTTONS or not 2 <= len(name) <= 60:
        await msg.answer("Введіть, будь ласка, коректне імʼя (від 2 до 60 символів).")
        return
    from shared import namefilter

    reason = namefilter.check_name(name)
    if reason:
        await _flag_abuse(msg, admin_bot)
        await msg.answer(namefilter.MESSAGES[reason])
        return
    async with session_scope() as session:
        client = await get_client(session, msg.from_user.id)
        if client is None:
            await state.clear()
            return
        await save_client(session, msg.from_user.id, name, client.phone)
    await state.clear()
    await msg.answer(f"Готово, тепер я звертаюсь до вас: {esc(name)} ✅", reply_markup=main_reply_kb())
