# admin_bot/handlers/more.py — статистика картинкою, таблиця записів, фото до/після, відповіді клієнтам
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message
from sqlalchemy import delete, select

from shared.database import session_scope
from shared.models import BookingStatus, ClientPhoto
from shared.services import get_bookings_in_period, get_client_by_id
from shared.tg import safe_edit
from shared.utils import esc, parse_cb_int, today_local

from ..keyboards import back, btn, cancel_input_kb, kb, menu_only_kb
from ..states import AnswerStates, ClientPhotoStates

logger = logging.getLogger(__name__)
router = Router(name="admin_more")

ICON = {
    BookingStatus.PENDING: "⏳",
    BookingStatus.CONFIRMED: "✅",
    BookingStatus.REJECTED: "❌",
    BookingStatus.CANCELED: "🚫",
    BookingStatus.NO_SHOW: "🙈",
}


# ---------- Статистика картинкою ----------


@router.callback_query(F.data.regexp(r"^st_img_\d+$"))
async def stats_image(call: CallbackQuery) -> None:
    from shared.stats_image import render_stats

    days = min(max(parse_cb_int(call.data, "st_img_") or 30, 7), 365)
    last = today_local()
    first = last - timedelta(days=days - 1)
    await call.answer("Малюю…")
    async with session_scope() as session:
        bookings = await get_bookings_in_period(session, first, last)
    try:
        png = await asyncio.to_thread(render_stats, bookings, first, last)
    except ImportError:
        await call.message.answer("Для картинки потрібна бібліотека Pillow. Перезапустіть start.bat.")
        return
    markup = kb(
        [
            [btn("7 днів", "st_img_7"), btn("30 днів", "st_img_30"), btn("90 днів", "st_img_90")],
            [back("rep_menu")],
        ]
    )
    for _ in (1, 2):
        try:
            await call.message.answer_photo(
                BufferedInputFile(png, filename="stats.jpg"),
                caption=f"📊 Статистика за {days} дн.",
                reply_markup=markup,
                request_timeout=90,
            )
            return
        except TelegramNetworkError:
            logger.warning("Статистика: мережева помилка Telegram")
    await call.message.answer("Картинка не завантажилась (повільна мережа). Ті самі цифри є текстом у «Цей місяць» і «Минулий тиждень».")


# ---------- Таблиця записів ----------

PAGE = 10


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


@router.callback_query(F.data.regexp(r"^tbl_\d+_\d+$"))
async def table_view(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    _, days_s, page_s = call.data.split("_")
    days, page = min(max(int(days_s), 1), 90), max(int(page_s), 0)
    first = today_local()
    last = first + timedelta(days=days - 1)
    async with session_scope() as session:
        rows = [
            b
            for b in await get_bookings_in_period(session, first, last)
            if b.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED)
        ]
    total = len(rows)
    pages = max((total + PAGE - 1) // PAGE, 1)
    page = min(page, pages - 1)
    chunk = rows[page * PAGE : (page + 1) * PAGE]
    lines = [f"📋 <b>Записи на {days} дн.</b> (всього {total}, сторінка {page + 1}/{pages})"]
    if chunk:
        table = ["№   дата   час    клієнт           послуга          грн"]
        for i, b in enumerate(chunk, 1):
            table.append(
                f"{ICON[b.status]}{i:<2} {b.date:%d.%m} {b.start_time:%H:%M} "
                f"{_cut(b.client_name, 15):<15} {_cut(b.title or '', 15):<15} {b.price if b.price is not None else '-'}"
            )
        lines.append("<pre>" + esc("\n".join(table)) + "</pre>")
        lines.append("Натисніть номер, щоб відкрити запис.")
    else:
        lines.append("Записів немає.")
    num_row = [btn(str(i), f"abk_view_{b.id}") for i, b in enumerate(chunk, 1)]
    rows_kb = [num_row[:5], num_row[5:]] if num_row else []
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"tbl_{days}_{page - 1}"))
    if page < pages - 1:
        nav.append(btn("▶️", f"tbl_{days}_{page + 1}"))
    if nav:
        rows_kb.append(nav)
    rows_kb.append([btn("7 дн.", "tbl_7_0"), btn("14 дн.", "tbl_14_0"), btn("30 дн.", "tbl_30_0")])
    rows_kb.append([back("rep_menu")])
    await call.answer()
    await safe_edit(call, "\n".join(lines), kb([r for r in rows_kb if r]))


# ---------- Фото до/після ----------


async def _photo_menu(client_id: int) -> tuple[str, object] | None:
    async with session_scope() as session:
        client = await get_client_by_id(session, client_id)
        if client is None:
            return None
        count = len(
            (await session.execute(select(ClientPhoto.id).where(ClientPhoto.client_id == client_id))).all()
        )
    consent = "✅ є" if client.photo_consent else "❌ немає"
    text = (
        f"📸 <b>Фото до/після: {esc(client.name)}</b>\n\n"
        f"Згода клієнта на фото: {consent}\n"
        f"Збережено фото: {count}\n\n"
        "Фото бачите лише ви. Зберігайте їх тільки за згодою клієнта."
    )
    rows = [
        [btn("✅ Згода є" if client.photo_consent else "☑️ Позначити, що згода є", f"cphc_{client_id}")],
        [btn("➕ Додати фото", f"cpha_{client_id}")],
    ]
    if count:
        rows.append([btn("🖼 Показати фото", f"cphs_{client_id}"), btn("🗑 Видалити", f"cphd_{client_id}")])
    rows.append([back(f"acl_{client_id}")])
    return text, kb(rows)


@router.callback_query(F.data.regexp(r"^cph_\d+$"))
async def photos_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    cid = parse_cb_int(call.data, "cph_") or 0
    view = await _photo_menu(cid)
    if view is None:
        await call.answer("Клієнта не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, *view)


@router.callback_query(F.data.regexp(r"^cphc_\d+$"))
async def photos_consent(call: CallbackQuery) -> None:
    cid = parse_cb_int(call.data, "cphc_") or 0
    async with session_scope() as session:
        client = await get_client_by_id(session, cid)
        if client is None:
            await call.answer("Клієнта не знайдено.", show_alert=True)
            return
        client.photo_consent = not client.photo_consent
    await call.answer("Збережено")
    view = await _photo_menu(cid)
    if view:
        await safe_edit(call, *view)


@router.callback_query(F.data.regexp(r"^cpha_\d+$"))
async def photos_add(call: CallbackQuery, state: FSMContext) -> None:
    cid = parse_cb_int(call.data, "cpha_") or 0
    async with session_scope() as session:
        client = await get_client_by_id(session, cid)
    if client is None:
        await call.answer("Клієнта не знайдено.", show_alert=True)
        return
    if not client.photo_consent:
        await call.answer("Спершу позначте, що клієнт дав згоду на фото.", show_alert=True)
        return
    await state.set_state(ClientPhotoStates.photo)
    await state.update_data(client_id=cid)
    await call.answer()
    await safe_edit(
        call,
        "Надішліть фото (як фото, не файлом). У підписі напишіть «до» або «після» і, за бажанням, коментар.\n"
        "Наприклад: «після, через 2 тижні».",
        cancel_input_kb(f"cph_{cid}"),
    )


@router.message(ClientPhotoStates.photo, F.photo)
async def photos_save(msg: Message, state: FSMContext) -> None:
    cid = (await state.get_data()).get("client_id")
    buf = await msg.bot.download(msg.photo[-1])
    data = buf.read() if buf else b""
    if not data or cid is None:
        await msg.answer("Не вдалося завантажити фото. Спробуйте ще раз.")
        return
    caption = (msg.caption or "").strip()
    low = caption.casefold()
    kind = "before" if low.startswith("до") else "after" if low.startswith("після") else "other"
    async with session_scope() as session:
        session.add(ClientPhoto(client_id=cid, data=data, kind=kind, note=caption[:200] or None))
    await msg.answer(
        "Фото збережено ✅ Надішліть ще одне або поверніться назад.",
        reply_markup=kb([[back(f"cph_{cid}")]]),
    )


@router.message(ClientPhotoStates.photo)
async def photos_wrong(msg: Message) -> None:
    await msg.answer("Надішліть саме фото (не файлом).")


KIND_UA = {"before": "ДО", "after": "ПІСЛЯ", "other": "Фото", "skin": "Шкіра"}


@router.callback_query(F.data.regexp(r"^cphs_\d+$"))
async def photos_show(call: CallbackQuery) -> None:
    cid = parse_cb_int(call.data, "cphs_") or 0
    async with session_scope() as session:
        photos = (
            await session.execute(
                select(ClientPhoto).where(ClientPhoto.client_id == cid).order_by(ClientPhoto.id.desc()).limit(10)
            )
        ).scalars().all()
    if not photos:
        await call.answer("Фото ще немає.", show_alert=True)
        return
    await call.answer()
    photos = list(reversed(photos))
    media = [
        InputMediaPhoto(
            media=BufferedInputFile(p.data, filename=f"p{p.id}.jpg"),
            caption=f"{KIND_UA.get(p.kind, 'Фото')} · {p.created_at:%d.%m.%Y}" + (f"\n{esc(p.note)}" if p.note else ""),
        )
        for p in photos
    ]
    try:
        if len(media) == 1:
            await call.message.answer_photo(media[0].media, caption=media[0].caption)
        else:
            await call.message.answer_media_group(media)
    except TelegramAPIError:
        logger.exception("Не вдалося показати фото клієнта")
        await call.message.answer("Не вдалося надіслати фото: спробуйте ще раз.")


@router.callback_query(F.data.regexp(r"^cphd_\d+$"))
async def photos_delete_list(call: CallbackQuery) -> None:
    await _delete_list(call, parse_cb_int(call.data, "cphd_") or 0)


async def _delete_list(call: CallbackQuery, cid: int) -> None:
    async with session_scope() as session:
        photos = (
            await session.execute(select(ClientPhoto).where(ClientPhoto.client_id == cid).order_by(ClientPhoto.id))
        ).scalars().all()
    if not photos:
        await call.answer("Фото немає.", show_alert=True)
        return
    rows = [
        [btn(f"🗑 {KIND_UA.get(p.kind, 'Фото')} {p.created_at:%d.%m.%Y}", f"cphx_{p.id}_{cid}")] for p in photos[:20]
    ]
    rows.append([back(f"cph_{cid}")])
    await call.answer()
    await safe_edit(call, "Яке фото видалити?", kb(rows))


@router.callback_query(F.data.regexp(r"^cphx_\d+_\d+$"))
async def photos_delete(call: CallbackQuery) -> None:
    _, pid, cid = call.data.split("_")
    async with session_scope() as session:
        await session.execute(delete(ClientPhoto).where(ClientPhoto.id == int(pid)))
    await call.answer("Видалено")
    await _delete_list(call, int(cid))


# ---------- Відповідь клієнту на його питання ----------


@router.callback_query(F.data.regexp(r"^ansr_-?\d+$"))
async def answer_start(call: CallbackQuery, state: FSMContext) -> None:
    client_tg = int(call.data.removeprefix("ansr_"))
    await state.set_state(AnswerStates.text)
    await state.update_data(client_tg=client_tg)
    await call.answer()
    rows = []
    try:
        from shared.models import QuickReply
        from sqlalchemy import select as _sel

        async with session_scope() as session:
            tpls = (await session.execute(_sel(QuickReply).order_by(QuickReply.id))).scalars().all()
        for t in tpls[:8]:
            rows.append([btn(t.text[:40], f"pro_use_{t.id}")])
    except Exception:
        logger.exception("Шаблони відповідей недоступні")
    rows.append([btn("✖️ Скасувати", "adm_menu")])
    await call.message.answer(
        "Напишіть відповідь, я передам її клієнту.\nАбо оберіть готовий шаблон:", reply_markup=kb(rows)
    )


@router.message(AnswerStates.text, F.text)
async def answer_send(msg: Message, state: FSMContext, client_bot: Bot | None = None) -> None:
    client_tg = (await state.get_data()).get("client_tg")
    await state.clear()
    if client_bot is None or client_tg is None or client_tg <= 0:
        await msg.answer("Не вдалося надіслати: клієнт не користується ботом. Зателефонуйте йому.")
        return
    try:
        await client_bot.send_message(client_tg, f"💬 <b>Відповідь майстра</b>\n\n{esc(msg.text.strip())}")
    except TelegramAPIError:
        await msg.answer("Не вдалося надіслати: клієнт міг заблокувати бота.")
        return
    try:
        from shared.models import ChatMessage

        async with session_scope() as session:
            session.add(ChatMessage(client_telegram_id=client_tg, direction="out", kind="text", text=msg.text.strip()[:1000]))
    except Exception:
        logger.exception("Відповідь не збережено в історії листування")
    await msg.answer("Відповідь надіслано ✅", reply_markup=menu_only_kb())
