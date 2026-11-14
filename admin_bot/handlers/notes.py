# admin_bot/handlers/notes.py — нотатки після візитів: можна написати одразу або пізніше
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared import visit_notes as vn
from shared.database import session_scope
from shared.models import Booking
from shared.tg import safe_edit
from shared.utils import esc, parse_cb_int

from ..keyboards import back, btn, cancel_input_kb, kb
from ..states import VisitNoteStates

logger = logging.getLogger(__name__)
router = Router(name="admin_notes")

PAGE = 8


async def _list_view() -> tuple[str, object]:
    items = await vn.pending_notes()
    if not items:
        return "📝 <b>Нотатки до візитів</b>\nУсе записано ✅ Нових візитів без нотаток немає.", kb([[back("adm_menu")]])
    rows = [
        [btn(f"📝 {b.client_name[:18]} · {b.date:%d.%m} {b.start_time:%H:%M}", f"vn_w_{b.id}")]
        for b in items[:PAGE]
    ]
    rows.append([back("adm_menu")])
    more = f"\n(показано {PAGE} з {len(items)})" if len(items) > PAGE else ""
    return (
        "📝 <b>Нотатки до візитів</b>\n"
        f"Без нотатки: {len(items)}. Оберіть візит і коротко напишіть, що робили й що врахувати наступного разу. "
        "Можна дописати пізніше, список зберігається 2 тижні." + more,
        kb(rows),
    )


@router.callback_query(F.data == "vn_list")
async def vn_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _list_view()
    await call.answer()
    await safe_edit(call, text, markup)


@router.callback_query(F.data.regexp(r"^vn_w_\d+$"))
async def vn_write(call: CallbackQuery, state: FSMContext) -> None:
    bid = parse_cb_int(call.data, "vn_w_") or 0
    async with session_scope() as session:
        b = await session.get(Booking, bid)
    if b is None:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await state.set_state(VisitNoteStates.text)
    await state.update_data(booking_id=bid)
    await call.answer()
    await safe_edit(
        call,
        f"📝 <b>{esc(b.client_name)}</b>, {b.date:%d.%m} {b.start_time:%H:%M}, {esc(b.title)}\n\n"
        "Напишіть нотатку одним повідомленням. Наступного разу я покажу її в картці запису цього клієнта.",
        kb([[btn("⏭ Без нотатки", f"vn_skip_{bid}")], [back("vn_list")]]),
    )


@router.message(VisitNoteStates.text, F.text)
async def vn_save(msg: Message, state: FSMContext) -> None:
    bid = (await state.get_data()).get("booking_id")
    await state.clear()
    if not bid or not await vn.save_note(int(bid), msg.text.strip()):
        await msg.answer("Не вдалося зберегти, спробуйте ще раз.")
        return
    text, markup = await _list_view()
    await msg.answer("Збережено ✅\n\n" + text, reply_markup=markup)


@router.callback_query(F.data.regexp(r"^vn_skip_\d+$"))
async def vn_skip(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await vn.save_note(parse_cb_int(call.data, "vn_skip_") or 0, None)
    await call.answer("Пропущено")
    text, markup = await _list_view()
    await safe_edit(call, text, markup)
