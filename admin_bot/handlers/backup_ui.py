# admin_bot/handlers/backup_ui.py — резервні копії: стан, надіслати, перевірити, відновити
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared import backup
from shared.scheduler import send_backup
from shared.tg import safe_edit
from shared.utils import esc

from ..keyboards import back, btn, cancel_input_kb, kb
from ..states import RestoreStates

logger = logging.getLogger(__name__)
router = Router(name="admin_backup")


def backup_menu_text() -> str:
    return "💾 <b>Резервні копії</b>\n\n" + esc(backup.status_lines()) + (
        "\n\nКопія робиться щодня вночі й надсилається вам. "
        "Файл у чаті можна зберегти собі й відновити з нього базу."
    )


def backup_menu_kb():
    return kb(
        [
            [btn("📤 Надіслати копію зараз", "bk_send")],
            [btn("🩺 Перевірити базу", "bk_check")],
            [btn("🔁 Відновити з копії", "bk_restore")],
            [back("adm_menu")],
        ]
    )


@router.callback_query(F.data == "bk_menu")
async def bk_menu(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, backup_menu_text(), backup_menu_kb())


@router.callback_query(F.data == "bk_send")
async def bk_send(call: CallbackQuery, is_super: bool = False) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await call.answer("Готую копію…")
    ok = await send_backup(call.bot, [call.from_user.id])
    await call.message.answer(
        "Копію надіслано ✅" if ok else
        "Telegram не прийняв файл (повільна мережа), але копію збережено на комп'ютері "
        "в папці backups поруч із ботом. Спробуйте ще раз пізніше.",
        reply_markup=kb([[back("bk_menu")]]),
    )


@router.callback_query(F.data == "bk_check")
async def bk_check(call: CallbackQuery, is_super: bool = False) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await call.answer("Перевіряю…")
    ok, msg = await asyncio.to_thread(backup.live_integrity)
    await safe_edit(
        call,
        ("✅ Базу перевірено: помилок немає.\n\n" if ok else f"⚠️ Проблема з базою: {esc(msg)}\n\n")
        + esc(backup.status_lines()),
        backup_menu_kb(),
    )


@router.callback_query(F.data == "bk_restore")
async def bk_restore(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    await state.set_state(RestoreStates.file)
    await call.answer()
    await safe_edit(
        call,
        "🔁 <b>Відновлення з копії</b>\n"
        "Надішліть сюди файл копії (<code>.zip</code> або <code>.db</code>), який бот присилав раніше. "
        "Я перевірю його й покажу, що в ньому, перед заміною. "
        "Поточну базу перед цим збережу в папку backups.",
        cancel_input_kb("bk_menu"),
    )


@router.message(RestoreStates.file, F.document)
async def bk_file(msg: Message, state: FSMContext, bot: Bot, is_super: bool = False) -> None:
    if not is_super:
        return
    doc = msg.document
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await msg.answer("Файл завеликий (понад 20 МБ).")
        return
    await msg.answer("Отримую файл, зачекайте…")
    try:
        buf = await bot.download(doc, timeout=120)
        data = buf.read()
    except Exception:
        logger.exception("Не вдалося завантажити файл копії")
        await msg.answer("Не вдалося отримати файл. Спробуйте ще раз.")
        return
    tmp = Path(tempfile.mkdtemp(prefix="salon_restore_"))
    db = backup.extract_db(data, tmp)
    if db is None:
        await msg.answer("Це не схоже на копію бази (потрібен .zip або .db від бота).")
        return
    ok, why, info = await asyncio.to_thread(backup.validate_db, db)
    if not ok:
        await msg.answer(f"Файл не підходить: {esc(why)}")
        return
    await state.update_data(path=str(db))
    await state.set_state(RestoreStates.confirm)
    await msg.answer(
        "У копії:\n"
        f"👥 клієнтів: {info['clients']}\n"
        f"📅 записів: {info['bookings']}\n"
        f"🕐 остання дата запису: {info['last'] or '—'}\n\n"
        "⚠️ Усе, що з'явилося в базі після цієї копії, буде втрачено. "
        "Поточну базу я збережу в папку backups. Після заміни бот сам перезапуститься.",
        reply_markup=kb(
            [[btn("✅ Так, відновити", "bk_yes")], [btn("❌ Скасувати", "bk_menu")]]
        ),
    )


@router.message(RestoreStates.file)
async def bk_not_file(msg: Message) -> None:
    await msg.answer("Надішліть саме файл копії (.zip або .db).")


@router.callback_query(RestoreStates.confirm, F.data == "bk_yes")
async def bk_yes(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    if not is_super:
        await call.answer("Лише для власника.", show_alert=True)
        return
    data = await state.get_data()
    path = Path(data.get("path", ""))
    await state.clear()
    if not path.exists():
        await call.answer("Файл загублено, почніть заново.", show_alert=True)
        return
    backup.stage_restore(path)
    await call.answer()
    await call.message.answer(
        "✅ Прийнято. Бот перезапускається й підставляє базу з копії. "
        "Через хвилину напишіть йому, щоб перевірити."
    )
    from shared import health

    health.mark_clean_exit()
    await asyncio.sleep(2)
    os._exit(0)  # start.bat сам запустить бота знову; на старті база замінюється
