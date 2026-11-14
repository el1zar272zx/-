# client_bot/handlers/start.py
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.services import get_client, save_client
from shared.tg import safe_edit
from shared.utils import esc, normalize_phone

from ..keyboards import contact_kb, main_reply_kb
from ..middleware import ASK_CONTACT

logger = logging.getLogger(__name__)
router = Router(name="client_start")

MENU_HINT = "Оберіть дію кнопками внизу 👇"


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await _welcome(msg, msg.from_user.id, ask_lang=True)


async def _welcome(msg: Message, uid: int, ask_lang: bool = False) -> None:
    """Вітання. Новому клієнту спочатку пропонуємо обрати мову."""
    from shared.i18n import PENDING

    async with session_scope() as session:
        client = await get_client(session, uid)
    if client is None:
        if ask_lang and uid not in PENDING:
            from .more import LANG_KB

            await msg.answer("🌐 Оберіть мову / Выберите язык:", reply_markup=LANG_KB)
            return
        await msg.answer(
            "Вітаю! 👋\nЦе бот запису до майстра.\n"
            "🌿 Оберіть послугу, дату й час, а ціну побачите на кожному кроці.\n\n" + ASK_CONTACT,
            reply_markup=contact_kb(),
        )
        return
    from shared.seasonal import greeting_line, usual_service

    extra = ""
    try:
        line = greeting_line(client)
        extra = f"\n{line}" if line else ""
    except Exception:
        logger.exception("Привітання не сформовано")
    await msg.answer(
        f"З поверненням, {esc(client.name)}! 👋{extra}\n{MENU_HINT}",
        reply_markup=main_reply_kb(),
    )
    try:
        svc = await usual_service(uid)
        if svc is not None:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            await msg.answer(
                "⭐ Записатися як зазвичай?",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"⭐ {svc.name[:40]} · {svc.duration_minutes} хв", callback_data=f"usual_{svc.id}")]
                    ]
                ),
            )
    except Exception:
        logger.exception("Кнопку «як зазвичай» не показано")


@router.message(F.contact)
async def got_contact(msg: Message, state: FSMContext) -> None:
    contact = msg.contact
    if contact.user_id != msg.from_user.id:
        await msg.answer(
            "Потрібен саме ваш контакт. Натисніть кнопку «📱 Поділитися контактом» внизу.",
            reply_markup=contact_kb(),
        )
        return
    phone = normalize_phone(contact.phone_number)
    if phone is None:
        await msg.answer(
            "Не вдалося прочитати номер. Спробуйте ще раз кнопкою внизу.",
            reply_markup=contact_kb(),
        )
        return
    phone = "+" + phone.lstrip("+")
    bad_name = False
    async with session_scope() as session:
        existing = await get_client(session, msg.from_user.id)
        if existing is not None:  # оновлення номера: імʼя лишається, яке задав клієнт
            name = existing.name
        else:
            name = " ".join(x for x in (contact.first_name, contact.last_name) if x).strip()
            name = (name or msg.from_user.full_name or "Клієнт")[:100]
            from shared import namefilter

            if namefilter.check_name(name):
                bad_name = True
                name = "Клієнт"
        await save_client(session, msg.from_user.id, name, phone)
        from shared.i18n import PENDING, set_cached_lang
        from shared.services import set_client_field

        chosen = PENDING.pop(msg.from_user.id, None)
        if chosen:  # мова, обрана на початку, зберігається в профілі
            await set_client_field(session, msg.from_user.id, lang=chosen)
            set_cached_lang(msg.from_user.id, chosen)
    await state.clear()
    if bad_name:
        from ..states import ProfileStates

        await state.set_state(ProfileStates.EDIT_NAME)
        await msg.answer(
            "Дякуємо, номер збережено ✅\nНапишіть, будь ласка, як до вас звертатися (справжнє імʼя):",
            reply_markup=main_reply_kb(),
        )
        return
    await msg.answer(
        f"Дякуємо, {esc(name)}! Номер збережено ✅\n{MENU_HINT}",
        reply_markup=main_reply_kb(),
    )


@router.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(MENU_HINT, reply_markup=main_reply_kb())


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data == "back_main_menu")
async def back_to_main_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, MENU_HINT)
