# client_bot/handlers/reviews.py — оцінка візиту
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from admin_bot.notify import notify_review
from shared.database import session_scope
from shared.services import add_review, set_review_comment
from shared.tg import safe_edit
from shared.utils import esc

from ..keyboards import BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_SERVICES, T, main_reply_kb
from ..states import ReviewStates

logger = logging.getLogger(__name__)
router = Router(name="client_reviews")

MENU_BUTTONS = set().union(*(T(b) for b in (BTN_BOOK, BTN_MY, BTN_PROFILE, BTN_SERVICES)))


@router.callback_query(F.data.regexp(r"^rate_\d+_[1-5]$"))
async def rate(
    call: CallbackQuery, state: FSMContext, admin_bot: Bot | None = None
) -> None:
    _, booking_id_s, rating_s = call.data.split("_")
    booking_id, rating = int(booking_id_s), int(rating_s)
    async with session_scope() as session:
        review = await add_review(session, booking_id, call.from_user.id, rating)
    if review is None:
        await call.answer("Дякуємо, відгук уже збережено.", show_alert=True)
        await safe_edit(call, "Дякуємо за відгук! 💛")
        return
    await call.answer("Дякуємо!")
    if rating >= 4:
        await safe_edit(call, f"Дякуємо за оцінку {'⭐' * rating}! Будемо раді бачити вас знову 💛")
    else:
        await state.set_state(ReviewStates.COMMENT)
        await state.update_data(review_booking_id=booking_id)
        await safe_edit(
            call,
            "Дякуємо за чесність. Напишіть, будь ласка, що можна покращити "
            "(або натисніть будь-яку кнопку меню, щоб пропустити):",
        )
    try:
        await notify_review(admin_bot, booking_id)
    except Exception:
        logger.exception("Не вдалося повідомити адмінів про відгук #%s", booking_id)


@router.message(ReviewStates.COMMENT, F.text)
async def review_comment(
    msg: Message, state: FSMContext, admin_bot: Bot | None = None
) -> None:
    data = await state.get_data()
    booking_id = data.get("review_booking_id")
    await state.clear()
    text = " ".join(msg.text.split())[:500]
    if booking_id is None or text.startswith("/") or text in MENU_BUTTONS:
        await msg.answer("Гаразд, дякуємо!", reply_markup=main_reply_kb())
        return
    async with session_scope() as session:
        review = await set_review_comment(session, booking_id, msg.from_user.id, text)
    await msg.answer("Дякуємо, ми врахуємо ваш коментар 🙏", reply_markup=main_reply_kb())
    if review is not None:
        try:
            await notify_review(admin_bot, booking_id)
        except Exception:
            logger.exception("Не вдалося повідомити адмінів про коментар #%s", booking_id)
