# admin_bot/handlers/studio.py — «🎵 Плейлисти для сеансів» і «📸 Пост для Instagram»
# (картинка Pillow + підпис; фон: колір салону, будь-яке своє фото чи фото салону)
from __future__ import annotations

import asyncio
import io
import logging
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, select

from shared import library, postgen, salon
from shared.database import session_scope
from shared.models import Playlist, SalonInfo, SalonPhoto, Service
from shared.pricing import eff_price, sale_active
from shared.services import get_active_cities, get_available_time_slots
from shared.tg import safe_edit
from shared.utils import WEEKDAYS_SHORT, esc, today_local

from ..keyboards import back, btn, cancel_input_kb, kb
from ..states import PlaylistStates, PostStates

logger = logging.getLogger(__name__)
router = Router(name="admin_studio")

PRO = "pro_menu"


# ======================= 🎵 Плейлисти =======================

PRESETS = (
    ("Спа-музика для релаксу", "https://www.youtube.com/results?search_query=spa+relaxation+music"),
    ("Музика для масажу", "https://www.youtube.com/results?search_query=massage+music"),
    ("Звуки природи", "https://www.youtube.com/results?search_query=relaxing+nature+sounds"),
    ("Тибетські чаші", "https://www.youtube.com/results?search_query=tibetan+singing+bowls"),
    ("Спокійне піаніно", "https://www.youtube.com/results?search_query=relaxing+piano+music"),
    ("Звуки дощу", "https://www.youtube.com/results?search_query=rain+sounds+for+relaxation"),
)


async def _seed_playlists() -> None:
    async with session_scope() as session:
        if await session.get(SalonInfo, "pl_seeded") is not None:
            return
        have = (await session.execute(select(Playlist.id).limit(1))).first()
        if have is None:
            for title, url in PRESETS:
                session.add(Playlist(title=title, url=url, note="пошукове посилання: додайте свої плейлисти"))
        session.add(SalonInfo(key="pl_seeded", value="1"))


async def _pl_screen(call: CallbackQuery) -> None:
    await _seed_playlists()
    async with session_scope() as session:
        rows = (await session.execute(select(Playlist).order_by(Playlist.id))).scalars().all()
    buttons = [[btn("🎶 " + p.title[:50], f"pl_v_{p.id}")] for p in rows]
    buttons.append([btn("➕ Додати плейлист", "pl_add")])
    buttons.append([back(PRO)])
    await safe_edit(
        call,
        "🎵 <b>Плейлисти для сеансів</b>\nОберіть музику для клієнта. Зараз у списку пошукові посилання на YouTube: "
        "додайте свої плейлисти (YouTube, Spotify, Apple Music), і вони будуть під рукою.",
        kb(buttons),
    )


@router.callback_query(F.data == "pl_menu")
async def pl_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _pl_screen(call)


@router.callback_query(F.data.regexp(r"^pl_v_\d+$"))
async def pl_view(call: CallbackQuery) -> None:
    pid = int(call.data.removeprefix("pl_v_"))
    async with session_scope() as session:
        p = await session.get(Playlist, pid)
    if p is None:
        await call.answer("Не знайдено.", show_alert=True)
        return
    await call.answer()
    rows = []
    if p.url.startswith(("http://", "https://")):
        rows.append([InlineKeyboardButton(text="▶️ Відкрити", url=p.url)])
    rows.append([btn("🗑 Видалити", f"pl_del_{p.id}")])
    rows.append([back("pl_menu")])
    await safe_edit(call, f"🎶 <b>{esc(p.title)}</b>\n{esc(p.note or '')}\n\n{esc(p.url)}", kb(rows))


@router.callback_query(F.data.regexp(r"^pl_del_\d+$"))
async def pl_delete(call: CallbackQuery) -> None:
    pid = int(call.data.removeprefix("pl_del_"))
    async with session_scope() as session:
        await session.execute(delete(Playlist).where(Playlist.id == pid))
    await call.answer("Видалено")
    await _pl_screen(call)


@router.callback_query(F.data == "pl_add")
async def pl_add(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PlaylistStates.title)
    await call.answer()
    await safe_edit(call, "Як назвати плейлист? Напишіть коротку назву.", cancel_input_kb("pl_menu"))


@router.message(PlaylistStates.title, F.text)
async def pl_title(msg: Message, state: FSMContext) -> None:
    title = msg.text.strip()
    if not 2 <= len(title) <= 100:
        await msg.answer("Назва має бути від 2 до 100 символів.")
        return
    await state.update_data(pl_title=title)
    await state.set_state(PlaylistStates.url)
    await msg.answer("Тепер надішліть посилання на плейлист (https://...).", reply_markup=cancel_input_kb("pl_menu"))


@router.message(PlaylistStates.url, F.text)
async def pl_url(msg: Message, state: FSMContext) -> None:
    url = msg.text.strip()
    if not url.startswith(("http://", "https://")) or len(url) > 400 or " " in url:
        await msg.answer("Це не схоже на посилання. Воно має починатися з https://")
        return
    title = (await state.get_data()).get("pl_title", "Плейлист")
    async with session_scope() as session:
        session.add(Playlist(title=title, url=url))
    await state.clear()
    await msg.answer("Плейлист додано ✅", reply_markup=kb([[btn("🎵 До плейлистів", "pl_menu")]]))


# ======================= 📸 Пост для Instagram =======================

_BOT_USER: dict[str, str] = {}


async def _bot_username(client_bot: Bot | None) -> str:
    if "u" in _BOT_USER:
        return _BOT_USER["u"]
    if client_bot is None:
        return ""
    try:
        me = await client_bot.get_me()
        _BOT_USER["u"] = me.username or ""
    except Exception:
        logger.exception("Не вдалося дізнатися імʼя клієнтського бота")
        return ""
    return _BOT_USER["u"]


async def _get(key: str, default: str = "") -> str:
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
    return default if row is None else row.value


async def _set(key: str, value: str) -> None:
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
        if row is None:
            session.add(SalonInfo(key=key, value=value))
        else:
            row.value = value


async def _cfg() -> dict:
    from shared.brand import brand as brand_name

    pal = await _get("ig_pal", "sage")
    size = await _get("ig_size", "post")
    return {
        "pal": pal if pal in postgen.PALETTES else "sage",
        "size": size if size in postgen.SIZES else "post",
        "brand": await brand_name(),
        "tags": await _get("ig_tags", postgen.DEFAULT_TAGS),
        "ctaimg": await _get("ig_ctaimg", "1") == "1",
    }


MENU_TEXT = (
    "📸 <b>Пост для Instagram</b>\n"
    "Оберіть, з чого зробити пост. Я підготую картинку й готовий підпис: залишиться лише опублікувати. "
    "Фон можна взяти будь-який: колір салону, своє фото чи фото салону."
)


def _menu_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("💆 З послуги", "ig_svc"), btn("⏰ Вільні вікна", "ig_win")],
            [btn("💡 З поради «Корисне»", "ig_tip"), btn("✏️ Свій текст", "ig_custom")],
            [btn("⚙️ Налаштування", "ig_set")],
            [back(PRO)],
        ]
    )


@router.callback_query(F.data == "ig_menu")
async def ig_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, MENU_TEXT, _menu_kb())


# ---------- вибір послуги / поради (з перегортанням) ----------

PAGE = 8


def _pager(items: list[tuple[int, str]], page: int, pick: str, nav: str) -> InlineKeyboardMarkup:
    total = max(1, (len(items) + PAGE - 1) // PAGE)
    page = max(0, min(page, total - 1))
    rows = [[btn(title[:50], f"{pick}{i}")] for i, title in items[page * PAGE : (page + 1) * PAGE]]
    nav_row = []
    if page > 0:
        nav_row.append(btn("◀️", f"{nav}{page - 1}"))
    nav_row.append(btn(f"{page + 1}/{total}", "noop"))
    if page < total - 1:
        nav_row.append(btn("▶️", f"{nav}{page + 1}"))
    rows.append(nav_row)
    rows.append([back("ig_menu")])
    return kb(rows)


async def _svc_items() -> list[tuple[int, str]]:
    async with session_scope() as session:
        rows = (
            await session.execute(select(Service).where(Service.is_active.is_(True)).order_by(Service.id))
        ).scalars().all()
    return [(s.id, s.name) for s in rows]


@router.callback_query(F.data == "ig_svc")
async def ig_svc(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(call, "💆 Оберіть послугу для поста:", _pager(await _svc_items(), 0, "ig_s_", "ig_sp_"))


@router.callback_query(F.data.regexp(r"^ig_sp_\d+$"))
async def ig_svc_page(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(
        call,
        "💆 Оберіть послугу для поста:",
        _pager(await _svc_items(), int(call.data.removeprefix("ig_sp_")), "ig_s_", "ig_sp_"),
    )


@router.callback_query(F.data.regexp(r"^ig_s_\d+$"))
async def ig_svc_pick(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    sid = int(call.data.removeprefix("ig_s_"))
    async with session_scope() as session:
        svc = await session.get(Service, sid)
    if svc is None:
        await call.answer("Послугу не знайдено.", show_alert=True)
        return
    cfg = await _cfg()
    user = await _bot_username(client_bot)
    tail = postgen.tail(user, salon.info("address"), salon.info("phone"), salon.info("instagram"), cfg["tags"])
    if svc.price is None:
        price_txt = ""
    elif sale_active(svc):
        price_txt = f"{svc.sale_price} грн (було {svc.price})"
    else:
        price_txt = f"{svc.price} грн"
    img_price = (price_txt + (f" · {svc.duration_minutes} хв" if svc.duration_minutes else "")).strip()
    caption = postgen.caption_service(svc.name, svc.description or "", svc.duration_minutes, price_txt, tail)
    await state.update_data(
        ig={
            "title": svc.name,
            "sub": postgen.trim(svc.description or "", 230),
            "price": img_price,
            "badge": "АКЦІЯ" if sale_active(svc) else "",
            "caption": caption,
            "pal": cfg["pal"],
            "size": cfg["size"],
            "dim": 2,
            "bg": None,
        }
    )
    await call.answer()
    await _ask_bg(call)


@router.callback_query(F.data == "ig_win")
async def ig_windows(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    today = today_local()
    lines: list[str] = []
    async with session_scope() as session:
        cities = await get_active_cities(session)
        city = cities[0].id if cities else None
        if city is not None:
            for i in range(0, 9):
                day = today + timedelta(days=i)
                slots = await get_available_time_slots(session, city_id=city, for_date=day, duration_minutes=60)
                if not slots:
                    continue
                if len(slots) > 5:
                    step = (len(slots) - 1) / 4
                    slots = [slots[round(j * step)] for j in range(5)]
                lines.append(f"{WEEKDAYS_SHORT[day.weekday()]} {day:%d.%m} — " + ", ".join(f"{t:%H:%M}" for t in slots))
                if len(lines) == 4:
                    break
    if not lines:
        await call.answer("Найближчим часом вільних вікон немає.", show_alert=True)
        return
    cfg = await _cfg()
    user = await _bot_username(client_bot)
    tail = postgen.tail(user, salon.info("address"), salon.info("phone"), salon.info("instagram"), cfg["tags"])
    await state.update_data(
        ig={
            "title": "Вільні вікна",
            "sub": "\n".join(lines),
            "price": "",
            "badge": "ВІЛЬНІ ВІКНА",
            "caption": postgen.caption_windows(lines, tail),
            "pal": cfg["pal"],
            "size": cfg["size"],
            "dim": 2,
            "bg": None,
        }
    )
    await call.answer()
    await _ask_bg(call)


async def _tip_items() -> list[tuple[int, str]]:
    async with session_scope() as session:
        rows = await library.items(session, "tip")
    return [(i.id, i.title) for i in rows]


@router.callback_query(F.data == "ig_tip")
async def ig_tip(call: CallbackQuery) -> None:
    items = await _tip_items()
    if not items:
        await call.answer("У «Корисне» поки немає порад.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, "💡 Оберіть пораду:", _pager(items, 0, "ig_t_", "ig_tp_"))


@router.callback_query(F.data.regexp(r"^ig_tp_\d+$"))
async def ig_tip_page(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(call, "💡 Оберіть пораду:", _pager(await _tip_items(), int(call.data.removeprefix("ig_tp_")), "ig_t_", "ig_tp_"))


@router.callback_query(F.data.regexp(r"^ig_t_\d+$"))
async def ig_tip_pick(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    async with session_scope() as session:
        item = await library.get(session, int(call.data.removeprefix("ig_t_")))
    if item is None:
        await call.answer("Не знайдено.", show_alert=True)
        return
    cfg = await _cfg()
    user = await _bot_username(client_bot)
    tail = postgen.tail(user, salon.info("address"), salon.info("phone"), salon.info("instagram"), cfg["tags"])
    await state.update_data(
        ig={
            "title": item.title,
            "sub": postgen.trim(item.text, 260),
            "price": "",
            "badge": "ПОРАДА",
            "caption": postgen.caption_tip(item.title, item.text, tail),
            "pal": cfg["pal"],
            "size": cfg["size"],
            "dim": 2,
            "bg": None,
        }
    )
    await call.answer()
    await _ask_bg(call)


# ---------- свій текст ----------


@router.callback_query(F.data == "ig_custom")
async def ig_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(PostStates.title)
    await call.answer()
    await safe_edit(call, "✏️ Напишіть заголовок поста (коротко, до 80 символів).", cancel_input_kb("ig_menu"))


@router.message(PostStates.title, F.text)
async def ig_custom_title(msg: Message, state: FSMContext) -> None:
    title = msg.text.strip()
    if not 2 <= len(title) <= 120:
        await msg.answer("Заголовок має бути від 2 до 120 символів.")
        return
    await state.update_data(c_title=title)
    await state.set_state(PostStates.body)
    await msg.answer(
        "Тепер напишіть основний текст (його видно на картинці й у підписі). Якщо текст не потрібен, надішліть «-».",
        reply_markup=cancel_input_kb("ig_menu"),
    )


@router.message(PostStates.body, F.text)
async def ig_custom_body(msg: Message, state: FSMContext, client_bot: Bot | None = None) -> None:
    body = msg.text.strip()
    if body in ("-", "—"):
        body = ""
    if len(body) > 900:
        await msg.answer("Занадто довго: до 900 символів.")
        return
    data = await state.get_data()
    title = data.get("c_title", "")
    cfg = await _cfg()
    user = await _bot_username(client_bot)
    tail = postgen.tail(user, salon.info("address"), salon.info("phone"), salon.info("instagram"), cfg["tags"])
    await state.set_state(None)
    await state.update_data(
        ig={
            "title": title,
            "sub": postgen.trim(body, 300),
            "price": "",
            "badge": "",
            "caption": postgen.caption_custom(title, body, tail),
            "pal": cfg["pal"],
            "size": cfg["size"],
            "dim": 2,
            "bg": None,
        }
    )
    await _ask_bg(msg)


# ---------- фон ----------


def _bg_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("🎨 Колір салону", "ig_bg_grad")],
            [btn("📷 Моє фото (будь-яка картинка)", "ig_bg_photo")],
            [btn("🏛 Фото салону", "ig_bg_salon")],
            [back("ig_menu")],
        ]
    )


BG_TEXT = "🖼 <b>Який фон зробити?</b>\nМожна взяти будь-яку свою картинку: текст ляже поверх неї."


async def _ask_bg(event) -> None:
    if isinstance(event, CallbackQuery):
        await safe_edit(event, BG_TEXT, _bg_kb())
    else:
        await event.answer(BG_TEXT, reply_markup=_bg_kb())


@router.callback_query(F.data == "ig_bg_grad")
async def ig_bg_grad(call: CallbackQuery, state: FSMContext) -> None:
    ig = (await state.get_data()).get("ig")
    if not ig:
        await call.answer("Почніть спочатку: меню «Пост для Instagram».", show_alert=True)
        return
    ig["bg"] = None
    await state.update_data(ig=ig)
    await call.answer("Роблю картинку…")
    await _show(call.bot, call.message.chat.id, state)


@router.callback_query(F.data == "ig_bg_photo")
async def ig_bg_photo(call: CallbackQuery, state: FSMContext) -> None:
    if not (await state.get_data()).get("ig"):
        await call.answer("Почніть спочатку: меню «Пост для Instagram».", show_alert=True)
        return
    await state.set_state(PostStates.photo)
    await call.answer()
    await safe_edit(
        call,
        "📷 Надішліть картинку для фону: як фото або як файл. Підійде будь-яка: з телефону, з роботи майстра, з інтернету.",
        cancel_input_kb("ig_menu"),
    )


def _prep_bg(raw: bytes) -> bytes:
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    img.thumbnail((2200, 2200))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


async def _take_photo(msg: Message, state: FSMContext, file_obj) -> None:
    buf = await msg.bot.download(file_obj)
    raw = buf.read() if buf else b""
    if not raw:
        await msg.answer("Не вдалося завантажити картинку. Спробуйте ще раз.")
        return
    try:
        bg = await asyncio.to_thread(_prep_bg, raw)
    except Exception:
        await msg.answer("Це не схоже на картинку. Надішліть фото у форматі JPG чи PNG.")
        return
    data = await state.get_data()
    ig = data.get("ig")
    if not ig:
        await state.clear()
        await msg.answer("Сеанс завершився. Почніть спочатку.", reply_markup=kb([[btn("📸 Пост", "ig_menu")]]))
        return
    ig["bg"] = bg
    await state.set_state(None)
    await state.update_data(ig=ig)
    await _show(msg.bot, msg.chat.id, state)


@router.message(PostStates.photo, F.photo)
async def ig_photo_in(msg: Message, state: FSMContext) -> None:
    await _take_photo(msg, state, msg.photo[-1])


@router.message(PostStates.photo, F.document)
async def ig_doc_in(msg: Message, state: FSMContext) -> None:
    doc = msg.document
    if not (doc.mime_type or "").startswith("image/"):
        await msg.answer("Надішліть саме картинку (JPG чи PNG).")
        return
    if (doc.file_size or 0) > 15 * 1024 * 1024:
        await msg.answer("Файл завеликий (понад 15 МБ). Надішліть менший.")
        return
    await _take_photo(msg, state, doc)


@router.message(PostStates.photo)
async def ig_photo_wrong(msg: Message) -> None:
    await msg.answer("Надішліть картинку як фото чи файл, або натисніть «Скасувати».")


@router.callback_query(F.data == "ig_bg_salon")
async def ig_bg_salon(call: CallbackQuery) -> None:
    async with session_scope() as session:
        rows = (await session.execute(select(SalonPhoto.id, SalonPhoto.caption).order_by(SalonPhoto.id))).all()
    if not rows:
        await call.answer("У розділі «Про салон» ще немає фото.", show_alert=True)
        return
    await call.answer()
    buttons = [
        [btn(f"🏛 Фото {n}" + (f": {cap[:30]}" if cap else ""), f"ig_ps_{pid}")]
        for n, (pid, cap) in enumerate(rows[:12], start=1)
    ]
    buttons.append([back("ig_menu")])
    await safe_edit(call, "🏛 Оберіть фото салону для фону:", kb(buttons))


@router.callback_query(F.data.regexp(r"^ig_ps_\d+$"))
async def ig_bg_salon_pick(call: CallbackQuery, state: FSMContext) -> None:
    ig = (await state.get_data()).get("ig")
    if not ig:
        await call.answer("Почніть спочатку: меню «Пост для Instagram».", show_alert=True)
        return
    async with session_scope() as session:
        photo = await session.get(SalonPhoto, int(call.data.removeprefix("ig_ps_")))
    if photo is None:
        await call.answer("Фото не знайдено.", show_alert=True)
        return
    try:
        ig["bg"] = await asyncio.to_thread(_prep_bg, photo.data)
    except Exception:
        await call.answer("Не вдалося відкрити це фото.", show_alert=True)
        return
    await state.update_data(ig=ig)
    await call.answer("Роблю картинку…")
    await _show(call.bot, call.message.chat.id, state)


# ---------- показ результату ----------


def _preview_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("🎨 Колір", "ig_pal"), btn("📐 Формат", "ig_size")],
            [btn("🌓 Темніше", "ig_dim_p"), btn("☀️ Світліше", "ig_dim_m")],
            [btn("✏️ Заголовок", "ig_et"), btn("📝 Підпис", "ig_ec")],
            [btn("🖼 Інший фон", "ig_bgc"), btn("🏷 Ярлик", "ig_badge")],
            [btn("📎 Файл без стиснення", "ig_file"), btn("✅ Готово", "ig_done")],
        ]
    )


async def _render(ig: dict) -> bytes:
    cfg = await _cfg()
    contact = salon.info("instagram") or salon.info("phone")
    cta = ""
    if cfg["ctaimg"]:
        user = await _bot_username(None)
        cta = f"Запис у Telegram: @{user}" if user else ""
    return await asyncio.to_thread(
        lambda: postgen.render(
            title=ig["title"],
            sub=ig.get("sub", ""),
            price=ig.get("price", ""),
            badge=ig.get("badge", ""),
            brand=cfg["brand"],
            contact=contact,
            cta=cta,
            palette=ig.get("pal", "sage"),
            size=ig.get("size", "post"),
            dim=ig.get("dim", 2),
            bg=ig.get("bg"),
        )
    )


async def _show(bot: Bot, chat_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    ig = data.get("ig")
    if not ig:
        return
    try:
        jpeg = await _render(ig)
    except Exception:
        logger.exception("Не вдалося намалювати пост")
        await bot.send_message(chat_id, "Не вдалося створити картинку. Спробуйте інший текст чи фон.")
        return
    for key in ("cap_id", "prev_id"):
        mid = ig.get(key)
        if mid:
            try:
                await bot.delete_message(chat_id, mid)
            except TelegramAPIError:
                pass
    cap = await bot.send_message(chat_id, esc(ig["caption"][:4000]))
    pal_name = postgen.PALETTES[ig.get("pal", "sage")][4]
    size_name = postgen.SIZES[ig.get("size", "post")][2]
    prev = await bot.send_photo(
        chat_id,
        BufferedInputFile(jpeg, "post.jpg"),
        caption=f"👆 Підпис для поста вище (скопіюйте його). Колір: {pal_name}, формат: {size_name}.",
        reply_markup=_preview_kb(),
    )
    ig["cap_id"], ig["prev_id"], ig["last"] = cap.message_id, prev.message_id, jpeg
    await state.update_data(ig=ig)


async def _need(call: CallbackQuery, state: FSMContext) -> dict | None:
    ig = (await state.get_data()).get("ig")
    if not ig:
        await call.answer("Сеанс завершено. Почніть спочатку.", show_alert=True)
        return None
    return ig


@router.callback_query(F.data == "ig_pal")
async def ig_pal(call: CallbackQuery, state: FSMContext) -> None:
    ig = await _need(call, state)
    if ig is None:
        return
    order = postgen.PALETTE_ORDER
    ig["pal"] = order[(order.index(ig.get("pal", "sage")) + 1) % len(order)]
    await state.update_data(ig=ig)
    await call.answer("Міняю колір…")
    await _show(call.bot, call.message.chat.id, state)


@router.callback_query(F.data == "ig_size")
async def ig_size(call: CallbackQuery, state: FSMContext) -> None:
    ig = await _need(call, state)
    if ig is None:
        return
    order = postgen.SIZE_ORDER
    ig["size"] = order[(order.index(ig.get("size", "post")) + 1) % len(order)]
    await state.update_data(ig=ig)
    await call.answer("Міняю формат…")
    await _show(call.bot, call.message.chat.id, state)


@router.callback_query(F.data.in_({"ig_dim_p", "ig_dim_m"}))
async def ig_dim(call: CallbackQuery, state: FSMContext) -> None:
    ig = await _need(call, state)
    if ig is None:
        return
    if not ig.get("bg"):
        await call.answer("Затемнення працює для фото-фону.", show_alert=True)
        return
    step = 1 if call.data == "ig_dim_p" else -1
    ig["dim"] = max(0, min(len(postgen.DIM_ALPHA) - 1, ig.get("dim", 2) + step))
    await state.update_data(ig=ig)
    await call.answer("Готово")
    await _show(call.bot, call.message.chat.id, state)


@router.callback_query(F.data == "ig_badge")
async def ig_badge(call: CallbackQuery, state: FSMContext) -> None:
    ig = await _need(call, state)
    if ig is None:
        return
    order = postgen.BADGES
    cur = ig.get("badge", "")
    ig["badge"] = order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]
    await state.update_data(ig=ig)
    await call.answer("Міняю ярлик…")
    await _show(call.bot, call.message.chat.id, state)


@router.callback_query(F.data == "ig_bgc")
async def ig_bg_change(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, state) is None:
        return
    await call.answer()
    await call.message.answer(BG_TEXT, reply_markup=_bg_kb())


@router.callback_query(F.data == "ig_et")
async def ig_edit_title(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, state) is None:
        return
    await state.set_state(PostStates.edit_title)
    await call.answer()
    await call.message.answer("✏️ Напишіть новий заголовок для картинки.", reply_markup=cancel_input_kb("ig_back"))


@router.message(PostStates.edit_title, F.text)
async def ig_edit_title_save(msg: Message, state: FSMContext) -> None:
    title = msg.text.strip()
    if not 2 <= len(title) <= 120:
        await msg.answer("Заголовок має бути від 2 до 120 символів.")
        return
    ig = (await state.get_data()).get("ig")
    if not ig:
        await state.clear()
        return
    ig["title"] = title
    await state.set_state(None)
    await state.update_data(ig=ig)
    await _show(msg.bot, msg.chat.id, state)


@router.callback_query(F.data == "ig_ec")
async def ig_edit_caption(call: CallbackQuery, state: FSMContext) -> None:
    if await _need(call, state) is None:
        return
    await state.set_state(PostStates.edit_caption)
    await call.answer()
    await call.message.answer(
        "📝 Надішліть новий текст підпису цілком (я замінюю весь підпис).", reply_markup=cancel_input_kb("ig_back")
    )


@router.message(PostStates.edit_caption, F.text)
async def ig_edit_caption_save(msg: Message, state: FSMContext) -> None:
    ig = (await state.get_data()).get("ig")
    if not ig:
        await state.clear()
        return
    ig["caption"] = msg.text.strip()[:2200]
    await state.set_state(None)
    await state.update_data(ig=ig)
    await _show(msg.bot, msg.chat.id, state)


@router.callback_query(F.data == "ig_back")
async def ig_cancel_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await call.answer()
    try:
        await call.message.delete()
    except TelegramAPIError:
        pass


@router.callback_query(F.data == "ig_file")
async def ig_file(call: CallbackQuery, state: FSMContext) -> None:
    ig = await _need(call, state)
    if ig is None or not ig.get("last"):
        return
    await call.answer()
    await call.message.answer_document(BufferedInputFile(ig["last"], "instagram_post.jpg"), caption="Файл без стиснення Telegram.")


@router.callback_query(F.data == "ig_done")
async def ig_done(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Готово")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
    await call.message.answer(
        "Готово 💛 Збережіть картинку та скопіюйте підпис із повідомлення вище.",
        reply_markup=kb([[btn("📸 Ще один пост", "ig_menu"), btn("🧰 Інструменти", PRO)]]),
    )


# ---------- налаштування ----------


async def _set_screen(call: CallbackQuery | Message) -> None:
    cfg = await _cfg()
    text = (
        "⚙️ <b>Налаштування постів</b>\n\n"
        f"🎨 Колір: {postgen.PALETTES[cfg['pal']][4]}\n"
        f"📐 Формат: {postgen.SIZES[cfg['size']][2]}\n"
        f"🏷 Назва салону на картинці: {esc(cfg['brand'])}\n"
        f"#️⃣ Хештеги: {esc(cfg['tags'])}\n"
        f"📲 Рядок «Запис у Telegram» на картинці: {'так' if cfg['ctaimg'] else 'ні'}\n\n"
        "Телефон і Instagram беруться з розділу «Про салон»."
    )
    markup = kb(
        [
            [btn("🎨 Змінити колір", "ig_spal"), btn("📐 Змінити формат", "ig_ssize")],
            [btn("🏷 Назва салону", "ig_sbrand"), btn("#️⃣ Хештеги", "ig_stags")],
            [btn(("✅ " if cfg["ctaimg"] else "🚫 ") + "Рядок запису на картинці", "ig_scta")],
            [back("ig_menu")],
        ]
    )
    if isinstance(call, CallbackQuery):
        await safe_edit(call, text, markup)
    else:
        await call.answer(text, reply_markup=markup)


@router.callback_query(F.data == "ig_set")
async def ig_set(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _set_screen(call)


@router.callback_query(F.data == "ig_spal")
async def ig_set_pal(call: CallbackQuery) -> None:
    cfg = await _cfg()
    order = postgen.PALETTE_ORDER
    await _set("ig_pal", order[(order.index(cfg["pal"]) + 1) % len(order)])
    await call.answer("Готово")
    await _set_screen(call)


@router.callback_query(F.data == "ig_ssize")
async def ig_set_size(call: CallbackQuery) -> None:
    cfg = await _cfg()
    order = postgen.SIZE_ORDER
    await _set("ig_size", order[(order.index(cfg["size"]) + 1) % len(order)])
    await call.answer("Готово")
    await _set_screen(call)


@router.callback_query(F.data == "ig_scta")
async def ig_set_cta(call: CallbackQuery) -> None:
    cfg = await _cfg()
    await _set("ig_ctaimg", "0" if cfg["ctaimg"] else "1")
    await call.answer("Готово")
    await _set_screen(call)


@router.callback_query(F.data == "ig_sbrand")
async def ig_set_brand(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.brand)
    await call.answer()
    await safe_edit(call, "🏷 Напишіть назву салону для картинок (до 40 символів).", cancel_input_kb("ig_set"))


@router.message(PostStates.brand, F.text)
async def ig_save_brand(msg: Message, state: FSMContext) -> None:
    value = msg.text.strip()
    if not 2 <= len(value) <= 40:
        await msg.answer("Назва має бути від 2 до 40 символів.")
        return
    await _set("brand", value)
    await state.clear()
    await _set_screen(msg)


@router.callback_query(F.data == "ig_stags")
async def ig_set_tags(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PostStates.tags)
    await call.answer()
    await safe_edit(
        call,
        "#️⃣ Напишіть хештеги через пробіл (наприклад: #масаж #косметологія #жовтіводи). Вони додаються під кожним постом.",
        cancel_input_kb("ig_set"),
    )


@router.message(PostStates.tags, F.text)
async def ig_save_tags(msg: Message, state: FSMContext) -> None:
    value = " ".join(msg.text.split())[:300]
    await _set("ig_tags", value)
    await state.clear()
    await _set_screen(msg)


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()
