# admin_bot/handlers/works.py — фото робіт для послуг («Приклади робіт» у каталозі клієнтів)
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

from shared.database import session_scope
from shared.services import get_service
from shared.tg import safe_edit
from shared.utils import esc, parse_cb_int
from shared.works import MAX_PER_SERVICE, add_work, count_works, delete_work, list_works

from ..keyboards import back, btn, kb
from ..states import WorkStates

logger = logging.getLogger(__name__)
router = Router(name="admin_works")


async def _view(service_id: int) -> tuple[str, list]:
    async with session_scope() as session:
        svc = await get_service(session, service_id)
        works = await list_works(session, service_id)
    if svc is None:
        return "Послугу не знайдено.", [[back("adm_menu")]]
    text = (
        f"📸 <b>Фото робіт: {esc(svc.name)}</b>\n\n"
        f"Фото: {len(works)} з {MAX_PER_SERVICE}\n"
        "Клієнти бачать їх у каталозі послуг, кнопка «📸 Приклади робіт».\n"
        "Додавайте тільки фото, на яких клієнт дозволив показати себе, або без обличчя."
    )
    rows = [[btn("➕ Додати фото", f"wrka_{service_id}")]]
    if works:
        rows.append([btn("👀 Переглянути", f"wrkv_{service_id}"), btn("🗑 Видалити", f"wrkl_{service_id}")])
    rows.append([back(f"acare_{service_id}")])
    return text, rows


@router.callback_query(F.data.regexp(r"^wrk_\d+$"))
async def works_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    sid = parse_cb_int(call.data, "wrk_") or 0
    text, rows = await _view(sid)
    await call.answer()
    await safe_edit(call, text, kb(rows))


@router.callback_query(F.data.regexp(r"^wrkv_\d+$"))
async def works_preview(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "wrkv_") or 0
    async with session_scope() as session:
        works = await list_works(session, sid)
    if not works:
        await call.answer("Фото ще немає.", show_alert=True)
        return
    await call.answer()
    try:
        if len(works) == 1:
            await call.message.answer_photo(
                BufferedInputFile(works[0].data, "work.jpg"), caption=works[0].caption or None
            )
        else:
            media = [
                InputMediaPhoto(media=BufferedInputFile(w.data, f"work{i}.jpg"), caption=w.caption or None)
                for i, w in enumerate(works[:10])
            ]
            await call.message.answer_media_group(media)
    except Exception:
        logger.exception("Не вдалося показати фото робіт")
        await call.message.answer("Не вдалося надіслати фото. Спробуйте ще раз трохи пізніше.")


@router.callback_query(F.data.regexp(r"^wrka_\d+$"))
async def works_add_start(call: CallbackQuery, state: FSMContext) -> None:
    sid = parse_cb_int(call.data, "wrka_") or 0
    async with session_scope() as session:
        n = await count_works(session, sid)
    if n >= MAX_PER_SERVICE:
        await call.answer(f"Максимум {MAX_PER_SERVICE} фото. Видаліть зайві.", show_alert=True)
        return
    await state.set_state(WorkStates.photo)
    await state.update_data(service_id=sid)
    await call.answer()
    await safe_edit(
        call,
        "Надішліть фото робіт (як фото, не файлом), можна по одному за раз. "
        "Підпис до фото побачать клієнти.\nКоли закінчите, натисніть «Готово».",
        kb([[btn("✅ Готово", f"wrk_{sid}")]]),
    )


@router.message(WorkStates.photo, F.photo)
async def works_add_save(msg: Message, state: FSMContext) -> None:
    sid = (await state.get_data()).get("service_id")
    if sid is None:
        await state.clear()
        return
    async with session_scope() as session:
        n = await count_works(session, sid)
    if n >= MAX_PER_SERVICE:
        await msg.answer(
            f"Максимум {MAX_PER_SERVICE} фото. Видаліть зайві.",
            reply_markup=kb([[btn("✅ Готово", f"wrk_{sid}")]]),
        )
        return
    buf = await msg.bot.download(msg.photo[-1])
    data = buf.read() if buf else b""
    if not data:
        await msg.answer("Не вдалося завантажити фото. Спробуйте ще раз.")
        return
    async with session_scope() as session:
        await add_work(session, sid, data, (msg.caption or "")[:200] or None)
    await msg.answer(
        f"Фото додано ({n + 1} з {MAX_PER_SERVICE}). Надішліть ще або натисніть «Готово».",
        reply_markup=kb([[btn("✅ Готово", f"wrk_{sid}")]]),
    )


@router.message(WorkStates.photo)
async def works_add_wrong(msg: Message) -> None:
    await msg.answer("Надішліть саме фото (не файлом) або натисніть «Готово».")


@router.callback_query(F.data.regexp(r"^wrkl_\d+$"))
async def works_del_list(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "wrkl_") or 0
    async with session_scope() as session:
        works = await list_works(session, sid)
    if not works:
        await call.answer("Фото ще немає.", show_alert=True)
        return
    rows = [
        [btn(f"🗑 Фото {i}" + (f": {w.caption[:25]}" if w.caption else ""), f"wrkd_{w.id}")]
        for i, w in enumerate(works, 1)
    ]
    rows.append([back(f"wrk_{sid}")])
    await call.answer()
    await safe_edit(call, "Яке фото видалити? (Номери як у «Переглянути».)", kb(rows))


@router.callback_query(F.data.regexp(r"^wrkd_\d+$"))
async def works_del(call: CallbackQuery) -> None:
    async with session_scope() as session:
        sid = await delete_work(session, parse_cb_int(call.data, "wrkd_") or 0)
    await call.answer("Видалено")
    text, rows = await _view(sid or 0)
    await safe_edit(call, text, kb(rows))
