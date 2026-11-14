# client_bot/handlers/care.py — «🌿 Мій догляд»: щоденник самопочуття, фото шкіри, паспорт шкіри,
# вподобання сеансу, челендж тижня, міні-курси, сумісність процедур, сезонна порада, «Мій рік»,
# листування з майстром, голосові повідомлення, «забули річ»
from __future__ import annotations

import logging
from datetime import timedelta, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import delete, select

from shared import clientdata as cd
from shared import selfcare, wellbeing, yearcard
from shared import selfcare_data as sd
from shared.database import session_scope
from shared.i18n import _lang_of
from shared.models import Booking, BookingStatus, ChatMessage, ClientPhoto, Service
from shared.services import get_active_admin_ids, get_client, set_client_field
from shared.tg import safe_edit
from shared.utils import esc, now_local, today_local

from ..keyboards import BTN_CARE, T, back_button, main_reply_kb
from ..states import LostStates, PhotoDiaryStates, SkinPassStates
from ..states import AskStates

logger = logging.getLogger(__name__)
router = Router(name="client_care")


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _k(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _hub_kb() -> InlineKeyboardMarkup:
    return _k(
        [
            [_b("💗 Щоденник самопочуття", "wb_menu"), _b("📸 Фото шкіри", "ph_menu")],
            [_b("🧴 Паспорт шкіри", "sp_menu"), _b("🎛 Вподобання сеансу", "cf_menu")],
            [_b("🏆 Челендж тижня", "ch_menu"), _b("📅 Міні-курси", "cr_menu")],
            [_b("🔎 Сумісність процедур", "cc_menu"), _b("🍂 Сезонна порада", "sc_season")],
            [_b("🎁 Мій рік з нами", "yr_show"), _b("📜 Листування з майстром", "ms_menu")],
            [_b("🧳 Забули річ у салоні?", "lost_start")],
            [back_button("back_main_menu")],
        ]
    )


HUB_TEXT = (
    "🌿 <b>Мій догляд</b>\n"
    "Ваші особисті інструменти: самопочуття, фото шкіри, челенджі, міні-курси та поради. "
    "Все це бачите лише ви (і майстер, якщо ви самі це дозволили)."
)


async def _hub(event, state: FSMContext) -> None:
    await state.clear()
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event, HUB_TEXT, _hub_kb())
    else:
        await event.answer(HUB_TEXT, reply_markup=_hub_kb())


@router.message(F.text.in_(T(BTN_CARE)))
async def hub_msg(msg: Message, state: FSMContext) -> None:
    await _hub(msg, state)


@router.callback_query(F.data == "sc_menu")
async def hub_cb(call: CallbackQuery, state: FSMContext) -> None:
    await _hub(call, state)


# ======================= 💗 Щоденник самопочуття =======================


def _score_kb(prefix: str, back: str = "wb_menu") -> InlineKeyboardMarkup:
    rows = [
        [_b(str(n), f"{prefix}{n}") for n in range(1, 6)],
        [_b(str(n), f"{prefix}{n}") for n in range(6, 11)],
        [back_button(back)],
    ]
    return _k(rows)


SCALE_HELP = "Оцініть біль чи напругу від 1 до 10: 1 — майже нічого не турбує, 10 — дуже сильно."


async def _context_booking(uid: int) -> tuple[str, int | None]:
    """До якого візиту «прив'язати» оцінку: найближчий у 24 год (до) або минулий за 48 год (після)."""
    now = now_local().replace(tzinfo=None)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Booking).where(
                    Booking.client_telegram_id == uid,
                    Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                    Booking.date >= (now - timedelta(days=3)).date(),
                    Booking.date <= (now + timedelta(days=2)).date(),
                )
            )
        ).scalars().all()
    best_before = None
    best_after = None
    for b in rows:
        start = datetime.combine(b.date, b.start_time)
        end = datetime.combine(b.date, b.end_time)
        if now <= start <= now + timedelta(hours=24):
            if best_before is None or start < best_before[0]:
                best_before = (start, b.id)
        elif end <= now <= end + timedelta(hours=48):
            if best_after is None or end > best_after[0]:
                best_after = (end, b.id)
    if best_before and not await wellbeing.has_entry(uid, best_before[1], "before"):
        return "before", best_before[1]
    if best_after and not await wellbeing.has_entry(uid, best_after[1], "after"):
        return "after", best_after[1]
    return "now", None


async def _wb_screen(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    entries = await wellbeing.recent(uid, 20)
    on = await cd.get(uid, "wb_on") == "1"
    text = (
        "💗 <b>Щоденник самопочуття</b>\n"
        "Записуйте, як почувається ваше тіло: так видно, чи допомагають процедури.\n\n"
        + wellbeing.summary(entries, lang)
    )
    rows = [[_b("➕ Записати зараз", "wb_add")]]
    if len(entries) >= 2:
        rows.append([_b("📈 Динаміка", "wb_chart")])
    rows.append([_b(("✅ " if on else "🚫 ") + "Нагадувати до й після візитів", "wb_tog")])
    rows.append([back_button("sc_menu")])
    await safe_edit(call, text, _k(rows))


@router.callback_query(F.data == "wb_menu")
async def wb_menu(call: CallbackQuery) -> None:
    await call.answer()
    await _wb_screen(call)


@router.callback_query(F.data == "wb_add")
async def wb_add(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(call, "💗 " + SCALE_HELP, _score_kb("wb_s_"))


@router.callback_query(F.data.regexp(r"^wb_s_(\d|10)$"))
async def wb_save_now(call: CallbackQuery) -> None:
    score = int(call.data.removeprefix("wb_s_"))
    uid = call.from_user.id
    kind, bid = await _context_booking(uid)
    await wellbeing.add(uid, score, kind, bid)
    await call.answer("Записано ✅")
    await _wb_screen(call)


@router.callback_query(F.data.regexp(r"^wb_(b|a)_\d+_(\d|10)$"))
async def wb_save_booking(call: CallbackQuery) -> None:
    _, kind_c, bid, score = call.data.split("_")
    uid = call.from_user.id
    kind = "before" if kind_c == "b" else "after"
    bid_i = int(bid)
    async with session_scope() as session:
        booking = await session.get(Booking, bid_i)
    if booking is None or booking.client_telegram_id != uid:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    if await wellbeing.has_entry(uid, bid_i, kind):
        await call.answer("Ви вже відповіли ✅")
        return
    await wellbeing.add(uid, int(score), kind, bid_i)
    await call.answer("Дякуємо ✅")
    entries = await wellbeing.recent(uid, 30)
    extra = ""
    if kind == "after":
        ds = [e for e in entries if e.booking_id == bid_i and e.kind == "before"]
        if ds:
            diff = int(score) - ds[-1].score
            if diff < 0:
                extra = "\nСтало легше на " + str(abs(diff)) + " 💚"
            elif diff == 0:
                extra = "\nОцінка така сама, як до візиту."
            else:
                extra = "\nМайстер врахує це наступного разу. Якщо болить сильніше, напишіть їй."
    try:
        await call.message.edit_text(
            f"💗 Записано: {score}/10. Дякуємо!{extra}",
            reply_markup=_k([[_b("📈 Мій щоденник", "wb_menu")]]),
        )
    except TelegramAPIError:
        pass


@router.callback_query(F.data == "wb_chart")
async def wb_chart(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    entries = await wellbeing.recent(uid, 20)
    if len(entries) < 2:
        await call.answer("Потрібно хоча б два записи.", show_alert=True)
        return
    await call.answer()
    data = wellbeing.chart(entries, lang)
    await call.message.answer_photo(
        BufferedInputFile(data, "wellbeing.png"),
        caption=wellbeing.summary(entries, lang),
        reply_markup=_k([[_b("⬅️ До щоденника", "wb_menu")]]),
    )


@router.callback_query(F.data == "wb_tog")
async def wb_toggle(call: CallbackQuery) -> None:
    uid = call.from_user.id
    on = await cd.get(uid, "wb_on") == "1"
    await cd.put(uid, "wb_on", "0" if on else "1")
    await call.answer("Нагадування вимкнено" if on else "Нагадаю напередодні візиту й наступного дня 🌿")
    await _wb_screen(call)


# ======================= 📸 Фото шкіри =======================


async def _skin_photos(uid: int) -> list[ClientPhoto]:
    async with session_scope() as session:
        client = await get_client(session, uid)
        if client is None:
            return []
        rows = (
            await session.execute(
                select(ClientPhoto)
                .where(ClientPhoto.client_id == client.id, ClientPhoto.kind == "skin")
                .order_by(ClientPhoto.id)
            )
        ).scalars().all()
    return list(rows)


async def _ph_screen(call: CallbackQuery) -> None:
    uid = call.from_user.id
    photos = await _skin_photos(uid)
    remind = await cd.get(uid, "ph_on") == "1"
    text = (
        "📸 <b>Фото шкіри</b>\n"
        "Знімайте обличчя раз на 2 тижні при однаковому світлі: так добре видно, як змінюється шкіра. "
        "Фото бачите ви та майстер, більше ніхто.\n\n"
        f"Збережено фото: {len(photos)}"
    )
    rows = [[_b("📸 Додати фото", "ph_add")]]
    if photos:
        rows.append([_b("🖼 Мої фото", "ph_list")])
    if len(photos) >= 2:
        rows.append([_b("↔️ Перше й останнє", "ph_cmp")])
    rows.append([_b(("✅ " if remind else "🚫 ") + "Нагадувати раз на 2 тижні", "ph_tog")])
    if photos:
        rows.append([_b("🗑 Видалити всі мої фото", "ph_delq")])
    rows.append([back_button("sc_menu")])
    await safe_edit(call, text, _k(rows))


@router.callback_query(F.data == "ph_menu")
async def ph_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _ph_screen(call)


async def _ph_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PhotoDiaryStates.PHOTO)
    await safe_edit(
        call,
        "📸 Надішліть фото (саме як фото, не файлом). Краще при денному світлі, без макіяжу й фільтрів.",
        _k([[back_button("ph_menu")]]),
    )


@router.callback_query(F.data == "ph_add")
async def ph_add(call: CallbackQuery, state: FSMContext) -> None:
    async with session_scope() as session:
        client = await get_client(session, call.from_user.id)
    if client is None:
        await call.answer()
        return
    await call.answer()
    if not client.photo_consent:
        await safe_edit(
            call,
            "📷 Щоб зберегти ваші фото, потрібна згода. Їх бачите лише ви та майстер. Дозволяєте?",
            _k([[_b("✅ Так, дозволяю", "ph_ok")], [_b("Не зараз", "ph_menu")]]),
        )
        return
    await _ph_ask(call, state)


@router.callback_query(F.data == "ph_ok")
async def ph_consent(call: CallbackQuery, state: FSMContext) -> None:
    async with session_scope() as session:
        await set_client_field(session, call.from_user.id, photo_consent=True)
    await call.answer("Дякуємо ✅")
    await _ph_ask(call, state)


@router.message(PhotoDiaryStates.PHOTO, F.photo)
async def ph_save(msg: Message, state: FSMContext) -> None:
    from shared.works import compress

    buf = await msg.bot.download(msg.photo[-1])
    raw = buf.read() if buf else b""
    if not raw:
        await msg.answer("Не вдалося завантажити фото. Спробуйте ще раз.")
        return
    jpeg = compress(raw)
    async with session_scope() as session:
        client = await get_client(session, msg.from_user.id)
        if client is None:
            await state.clear()
            return
        session.add(ClientPhoto(client_id=client.id, data=jpeg, kind="skin", note=f"щоденник шкіри {today_local():%d.%m.%Y}"))
    await cd.put(msg.from_user.id, "ph_rem", today_local().isoformat())
    await state.clear()
    await msg.answer(
        "Фото додано в щоденник ✅ Наступне зробіть приблизно через 2 тижні.",
        reply_markup=_k([[_b("📸 Фото шкіри", "ph_menu")]]),
    )


@router.message(PhotoDiaryStates.PHOTO)
async def ph_wrong(msg: Message) -> None:
    await msg.answer("Надішліть саме фото (не файлом) або поверніться назад.")


@router.callback_query(F.data == "ph_list")
async def ph_list(call: CallbackQuery) -> None:
    photos = (await _skin_photos(call.from_user.id))[-6:]
    if not photos:
        await call.answer("Фото ще немає.", show_alert=True)
        return
    await call.answer()
    media = [
        InputMediaPhoto(media=BufferedInputFile(p.data, f"skin{p.id}.jpg"), caption=f"{p.created_at:%d.%m.%Y}")
        for p in photos
    ]
    try:
        if len(media) == 1:
            await call.message.answer_photo(media[0].media, caption=media[0].caption)
        else:
            await call.message.answer_media_group(media)
    except TelegramAPIError:
        logger.exception("Не вдалося надіслати фото шкіри")


@router.callback_query(F.data == "ph_cmp")
async def ph_compare(call: CallbackQuery) -> None:
    photos = await _skin_photos(call.from_user.id)
    if len(photos) < 2:
        await call.answer("Потрібно хоча б два фото.", show_alert=True)
        return
    await call.answer()
    first, last = photos[0], photos[-1]
    media = [
        InputMediaPhoto(media=BufferedInputFile(first.data, "first.jpg"), caption=f"Було: {first.created_at:%d.%m.%Y}"),
        InputMediaPhoto(media=BufferedInputFile(last.data, "last.jpg"), caption=f"Зараз: {last.created_at:%d.%m.%Y}"),
    ]
    try:
        await call.message.answer_media_group(media)
    except TelegramAPIError:
        logger.exception("Не вдалося надіслати порівняння фото")


@router.callback_query(F.data == "ph_tog")
async def ph_toggle(call: CallbackQuery) -> None:
    uid = call.from_user.id
    on = await cd.get(uid, "ph_on") == "1"
    await cd.put(uid, "ph_on", "0" if on else "1")
    if not on:
        await cd.put(uid, "ph_rem", today_local().isoformat())
    await call.answer("Вимкнено" if on else "Нагадаю через 2 тижні 🌿")
    await _ph_screen(call)


@router.callback_query(F.data == "ph_delq")
async def ph_delete_ask(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(
        call,
        "Видалити всі фото щоденника шкіри? Це не можна скасувати.",
        _k([[_b("🗑 Так, видалити", "ph_del")], [_b("Ні, залишити", "ph_menu")]]),
    )


@router.callback_query(F.data == "ph_del")
async def ph_delete(call: CallbackQuery) -> None:
    async with session_scope() as session:
        client = await get_client(session, call.from_user.id)
        if client is not None:
            await session.execute(
                delete(ClientPhoto).where(ClientPhoto.client_id == client.id, ClientPhoto.kind == "skin")
            )
    await call.answer("Фото видалено")
    await _ph_screen(call)


# ======================= 🧴 Паспорт шкіри =======================


async def _passport_text(uid: int, lang: str) -> tuple[str, dict]:
    ru = lang == "ru"
    data = await cd.get_json(uid, "skin_pass")
    li = 1 if ru else 0
    lines = ["🧴 <b>Паспорт шкіри</b>", "Майстер бачить його перед візитом і підбирає догляд саме під вас.", ""]
    lines.append("Тип шкіри: " + (sd.skin_label(data["type"], li) if data.get("type") else "не вказано"))
    lines.append("Алергії та чутливість: " + (esc(data["allergy"]) if data.get("allergy") else "не вказано"))
    lines.append("Реакції на процедури: " + (esc(data["react"]) if data.get("react") else "не вказано"))
    return "\n".join(lines), data


async def _sp_screen(call: CallbackQuery | Message, uid: int) -> None:
    lang = await _lang_of(uid)
    text, data = await _passport_text(uid, lang)
    cur = data.get("type")
    rows = [
        [_b(("✅ " if cur == code else "") + labels[0], f"sp_t_{code}") for code, labels in sd.SKIN_TYPES[:3]],
        [_b(("✅ " if cur == code else "") + labels[0], f"sp_t_{code}") for code, labels in sd.SKIN_TYPES[3:]],
        [_b("✍️ Алергії та чутливість", "sp_al")],
        [_b("✍️ Реакції на процедури", "sp_re")],
        [_b("🧴 Пройти тест типу шкіри", "skin_start")],
        [_b("🗑 Очистити паспорт", "sp_clr")],
        [back_button("sc_menu")],
    ]
    if isinstance(call, CallbackQuery):
        await safe_edit(call, text, _k(rows))
    else:
        await call.answer(text, reply_markup=_k(rows))


@router.callback_query(F.data == "sp_menu")
async def sp_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _sp_screen(call, call.from_user.id)


@router.callback_query(F.data.regexp(r"^sp_t_[a-z]+$"))
async def sp_type(call: CallbackQuery) -> None:
    code = call.data.removeprefix("sp_t_")
    if code not in {c for c, _ in sd.SKIN_TYPES}:
        await call.answer()
        return
    data = await cd.get_json(call.from_user.id, "skin_pass")
    data["type"] = code
    await cd.put_json(call.from_user.id, "skin_pass", data)
    await call.answer("Збережено ✅")
    await _sp_screen(call, call.from_user.id)


@router.callback_query(F.data.in_({"sp_al", "sp_re"}))
async def sp_ask(call: CallbackQuery, state: FSMContext) -> None:
    allergy = call.data == "sp_al"
    await state.set_state(SkinPassStates.ALLERGY if allergy else SkinPassStates.REACT)
    await call.answer()
    await safe_edit(
        call,
        (
            "✍️ Напишіть одним повідомленням, на що в вас алергія чи чутливість (наприклад: «на лаванду, на нікель»). "
            if allergy
            else "✍️ Напишіть, як шкіра реагувала на процедури (наприклад: «після пілінгу тривало почервоніння»). "
        )
        + "Щоб прибрати запис, надішліть «-».",
        _k([[back_button("sp_menu")]]),
    )


async def _sp_save(msg: Message, state: FSMContext, field: str) -> None:
    from shared import namefilter

    text = (msg.text or "").strip()
    if namefilter.has_obscene(text):
        await msg.answer(namefilter.TEXT_REJECT)
        return
    data = await cd.get_json(msg.from_user.id, "skin_pass")
    if text in ("-", "—", ""):
        data.pop(field, None)
    else:
        data[field] = text[:300]
    await cd.put_json(msg.from_user.id, "skin_pass", data)
    await state.clear()
    await msg.answer("Збережено ✅")
    await _sp_screen(msg, msg.from_user.id)


@router.message(SkinPassStates.ALLERGY, F.text)
async def sp_save_allergy(msg: Message, state: FSMContext) -> None:
    await _sp_save(msg, state, "allergy")


@router.message(SkinPassStates.REACT, F.text)
async def sp_save_react(msg: Message, state: FSMContext) -> None:
    await _sp_save(msg, state, "react")


@router.callback_query(F.data == "sp_clr")
async def sp_clear(call: CallbackQuery) -> None:
    await cd.drop(call.from_user.id, "skin_pass")
    await call.answer("Паспорт очищено")
    await _sp_screen(call, call.from_user.id)


# ======================= 🎛 Вподобання сеансу =======================


async def _cf_text(uid: int, lang: str) -> str:
    li = 1 if lang == "ru" else 0
    data = await cd.get_json(uid, "comfort")
    lines = ["🎛 <b>Вподобання сеансу</b>", "Майстер врахує їх, щоб вам було максимально комфортно.", ""]
    if not data:
        lines.append("Поки що не заповнено.")
    else:
        for key, _q, _o in sd.COMFORT:
            if key in data:
                lines.append(f"{sd.COMFORT_LABELS[key][li]}: {sd.comfort_label(key, data[key], li)}")
    return "\n".join(lines)


@router.callback_query(F.data == "cf_menu")
async def cf_menu(call: CallbackQuery) -> None:
    lang = await _lang_of(call.from_user.id)
    await call.answer()
    await safe_edit(
        call,
        await _cf_text(call.from_user.id, lang),
        _k([[_b("✏️ Заповнити чи змінити", "cf_q_0")], [back_button("sc_menu")]]),
    )


async def _cf_show(call: CallbackQuery, qi: int) -> None:
    lang = await _lang_of(call.from_user.id)
    _key, question, opts = sd.COMFORT[qi]
    rows = [[_b(sd.pick(labels, lang), f"cf_a_{qi}_{code}")] for code, labels in opts]
    rows.append([back_button("cf_menu")])
    await safe_edit(call, f"🎛 {qi + 1}/{len(sd.COMFORT)}. {sd.pick(question, lang)}", _k(rows))


@router.callback_query(F.data.regexp(r"^cf_q_\d$"))
async def cf_question(call: CallbackQuery) -> None:
    qi = int(call.data.removeprefix("cf_q_"))
    await call.answer()
    if qi < len(sd.COMFORT):
        await _cf_show(call, qi)


@router.callback_query(F.data.regexp(r"^cf_a_\d_[a-z]+$"))
async def cf_answer(call: CallbackQuery) -> None:
    _, _, qi_s, code = call.data.split("_")
    qi = int(qi_s)
    if qi >= len(sd.COMFORT):
        await call.answer()
        return
    key, _q, opts = sd.COMFORT[qi]
    if code not in {c for c, _ in opts}:
        await call.answer()
        return
    uid = call.from_user.id
    data = await cd.get_json(uid, "comfort")
    data[key] = code
    await cd.put_json(uid, "comfort", data)
    await call.answer("Збережено ✅")
    if qi + 1 < len(sd.COMFORT):
        await _cf_show(call, qi + 1)
        return
    lang = await _lang_of(uid)
    await safe_edit(
        call,
        await _cf_text(uid, lang) + "\n\nДякуємо! Майстер побачить це перед візитом 💛",
        _k([[back_button("sc_menu")]]),
    )


# ======================= 🏆 Челендж тижня =======================


async def _ch_screen(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    today = today_local()
    ch = selfcare.challenge_of(today)
    st = await selfcare.ch_load(uid)
    text = f"🏆 <b>Челендж тижня: {sd.pick(ch['title'], lang)}</b>\n\n{sd.pick(ch['text'], lang)}"
    rows: list[list[InlineKeyboardButton]] = []
    if st.get("joined"):
        days = st.get("days", [])
        text += f"\n\nПрогрес: {selfcare.ch_bar(st)}  ({len(days)}/7)"
        streak = int(st.get("streak", 0))
        if streak:
            text += f"\n🔥 Серія тижнів: {streak}"
        text += "\nЗараховується тиждень, де зроблено 5 днів із 7."
        if today.isoformat() in days:
            rows.append([_b("✅ Сьогодні вже відмічено", "noop")])
        else:
            rows.append([_b("✅ Сьогодні зроблено", "ch_done")])
        remind = bool(st.get("remind", 1))
        rows.append([_b(("✅ " if remind else "🚫 ") + "Нагадування о 19:00", "ch_rem")])
        rows.append([_b("🚪 Вийти з челенджу", "ch_leave")])
    else:
        text += "\n\nЧелендж необов'язковий: просто щодня відмічайте, що зробили."
        rows.append([_b("🚀 Беру участь", "ch_join")])
    rows.append([back_button("sc_menu")])
    await safe_edit(call, text, _k(rows))


@router.callback_query(F.data == "ch_menu")
async def ch_menu(call: CallbackQuery) -> None:
    await call.answer()
    await _ch_screen(call)


@router.callback_query(F.data == "ch_join")
async def ch_join(call: CallbackQuery) -> None:
    await selfcare.ch_join(call.from_user.id)
    await call.answer("Чудово, починаємо! 🚀")
    await _ch_screen(call)


@router.callback_query(F.data == "ch_done")
async def ch_done(call: CallbackQuery) -> None:
    st, new, week_done = await selfcare.ch_mark(call.from_user.id)
    if not new:
        await call.answer("Сьогодні вже відмічено ✅")
    elif week_done:
        await call.answer("🎉 Тиждень пройдено повністю!", show_alert=True)
    else:
        await call.answer("Зараховано ✅")
    await _ch_screen(call)


@router.callback_query(F.data == "ch_rem")
async def ch_remind_toggle(call: CallbackQuery) -> None:
    uid = call.from_user.id
    st = await selfcare.ch_load(uid)
    if not st:
        await call.answer()
        return
    st["remind"] = 0 if st.get("remind", 1) else 1
    await cd.put_json(uid, "ch", st)
    await call.answer("Готово")
    await _ch_screen(call)


@router.callback_query(F.data == "ch_leave")
async def ch_leave(call: CallbackQuery) -> None:
    st = await cd.get_json(call.from_user.id, "ch")
    if st:
        st["joined"] = 0
        await cd.put_json(call.from_user.id, "ch", st)
    await call.answer("Ви вийшли з челенджу")
    await _ch_screen(call)


# ======================= 📅 Міні-курси =======================


async def _cr_screen(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    st = await selfcare.course_state(uid)
    rows = []
    text = "📅 <b>Міні-курси</b>\nПо одному короткому завданню на день, 5 днів. Нове надсилаю вранці."
    if st.get("slug") in sd.COURSES:
        rows.append([_b("▶️ Мій курс: " + sd.pick(sd.COURSES[st["slug"]]["title"], lang), "cr_cont")])
    for slug, c in sd.COURSES.items():
        rows.append([_b(sd.pick(c["title"], lang), f"cr_v_{slug}")])
    rows.append([back_button("sc_menu")])
    await safe_edit(call, text, _k(rows))


@router.callback_query(F.data == "cr_menu")
async def cr_menu(call: CallbackQuery) -> None:
    await call.answer()
    await _cr_screen(call)


@router.callback_query(F.data.regexp(r"^cr_v_[a-z]+$"))
async def cr_view(call: CallbackQuery) -> None:
    slug = call.data.removeprefix("cr_v_")
    if slug not in sd.COURSES:
        await call.answer()
        return
    lang = await _lang_of(call.from_user.id)
    c = sd.COURSES[slug]
    await call.answer()
    await safe_edit(
        call,
        f"<b>{sd.pick(c['title'], lang)}</b>\n\nКурс на 5 днів. Кожен день: коротке завдання на кілька хвилин. "
        "Перший день одразу, далі щоранку.",
        _k([[_b("🚀 Почати", f"cr_s_{slug}")], [back_button("cr_menu")]]),
    )


@router.callback_query(F.data.regexp(r"^cr_s_[a-z]+$"))
async def cr_start(call: CallbackQuery) -> None:
    slug = call.data.removeprefix("cr_s_")
    if slug not in sd.COURSES:
        await call.answer()
        return
    uid = call.from_user.id
    lang = await _lang_of(uid)
    await selfcare.course_start(uid, slug)
    await call.answer("Починаємо!")
    await safe_edit(
        call,
        selfcare.course_day_text(slug, 1, lang) + "\n\nНаступний день надішлю завтра вранці 🌿",
        _k([[_b("⏸ Зупинити курс", "cr_stop")], [back_button("cr_menu")]]),
    )


@router.callback_query(F.data == "cr_cont")
async def cr_continue(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    st = await selfcare.course_state(uid)
    slug = st.get("slug")
    if slug not in sd.COURSES:
        await call.answer("Активного курсу немає.", show_alert=True)
        return
    nxt = await selfcare.course_advance(uid)
    await call.answer()
    if nxt is None:
        day = int(st.get("day", 1))
        await safe_edit(
            call,
            selfcare.course_day_text(slug, day, lang) + "\n\nНаступний день буде завтра вранці 🌿",
            _k([[_b("⏸ Зупинити курс", "cr_stop")], [back_button("cr_menu")]]),
        )
        return
    slug, day, last = nxt
    tail = "\n\n🎉 Курс завершено! Ви молодець. Повторюйте комплекс через день." if last else "\n\nНаступний день буде завтра вранці 🌿"
    await safe_edit(call, selfcare.course_day_text(slug, day, lang) + tail, _k([[back_button("cr_menu")]]))


@router.callback_query(F.data == "cr_stop")
async def cr_stop(call: CallbackQuery) -> None:
    await selfcare.course_stop(call.from_user.id)
    await call.answer("Курс зупинено")
    await _cr_screen(call)


# ======================= 🔎 Сумісність процедур =======================

PAGE = 8


async def _active_services() -> list[Service]:
    async with session_scope() as session:
        rows = (
            await session.execute(select(Service).where(Service.is_active.is_(True)).order_by(Service.id))
        ).scalars().all()
    return list(rows)


def _pager(items: list[Service], page: int, pick: str, nav: str, back: str) -> InlineKeyboardMarkup:
    total = (len(items) + PAGE - 1) // PAGE
    page = max(0, min(page, total - 1))
    chunk = items[page * PAGE : (page + 1) * PAGE]
    rows = [[_b(s.name[:48], f"{pick}{s.id}")] for s in chunk]
    nav_row = []
    if page > 0:
        nav_row.append(_b("◀️", f"{nav}{page - 1}"))
    nav_row.append(_b(f"{page + 1}/{total}", "noop"))
    if page < total - 1:
        nav_row.append(_b("▶️", f"{nav}{page + 1}"))
    rows.append(nav_row)
    rows.append([back_button(back)])
    return _k(rows)


@router.callback_query(F.data == "cc_menu")
async def cc_menu(call: CallbackQuery) -> None:
    await call.answer()
    items = await _active_services()
    await safe_edit(
        call,
        "🔎 <b>Сумісність процедур</b>\nОберіть першу процедуру, потім другу, і я підкажу, як їх краще поєднувати.",
        _pager(items, 0, "cc_1_", "cc_p_", "sc_menu"),
    )


@router.callback_query(F.data.regexp(r"^cc_p_\d+$"))
async def cc_page(call: CallbackQuery) -> None:
    page = int(call.data.removeprefix("cc_p_"))
    items = await _active_services()
    await call.answer()
    await safe_edit(
        call,
        "🔎 <b>Сумісність процедур</b>\nОберіть першу процедуру:",
        _pager(items, page, "cc_1_", "cc_p_", "sc_menu"),
    )


@router.callback_query(F.data.regexp(r"^cc_1_\d+$"))
async def cc_first(call: CallbackQuery) -> None:
    sid = int(call.data.removeprefix("cc_1_"))
    items = await _active_services()
    first = next((s for s in items if s.id == sid), None)
    if first is None:
        await call.answer("Послугу не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(
        call,
        f"🔎 Перша процедура: <b>{esc(first.name)}</b>\nТепер оберіть другу:",
        _pager(items, 0, f"cc_r_{sid}_", f"cc_q_{sid}_", "cc_menu"),
    )


@router.callback_query(F.data.regexp(r"^cc_q_\d+_\d+$"))
async def cc_second_page(call: CallbackQuery) -> None:
    _, _, sid_s, page_s = call.data.split("_")
    sid, page = int(sid_s), int(page_s)
    items = await _active_services()
    first = next((s for s in items if s.id == sid), None)
    if first is None:
        await call.answer()
        return
    await call.answer()
    await safe_edit(
        call,
        f"🔎 Перша процедура: <b>{esc(first.name)}</b>\nТепер оберіть другу:",
        _pager(items, page, f"cc_r_{sid}_", f"cc_q_{sid}_", "cc_menu"),
    )


@router.callback_query(F.data.regexp(r"^cc_r_\d+_\d+$"))
async def cc_result(call: CallbackQuery) -> None:
    _, _, a_s, b_s = call.data.split("_")
    a_id, b_id = int(a_s), int(b_s)
    items = await _active_services()
    sa = next((s for s in items if s.id == a_id), None)
    sb = next((s for s in items if s.id == b_id), None)
    if sa is None or sb is None:
        await call.answer("Послугу не знайдено.", show_alert=True)
        return
    lang = await _lang_of(call.from_user.id)
    verdict, text = sd.compat(sa.name, sb.name)
    await call.answer()
    msg = (
        f"🔎 <b>{esc(sa.name)}</b> + <b>{esc(sb.name)}</b>\n\n"
        f"{sd.VERDICT_ICON[verdict]} <b>{sd.pick(sd.VERDICT_TITLE[verdict], lang)}</b>\n"
        f"{sd.pick(text, lang)}\n\n<i>{sd.pick(sd.COMPAT_NOTE, lang)}</i>"
    )
    await safe_edit(
        call,
        msg,
        _k(
            [
                [_b("💬 Уточнити в майстра", f"cc_ask_{a_id}_{b_id}")],
                [_b("🔁 Інша пара", "cc_menu")],
                [back_button("sc_menu")],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^cc_ask_\d+_\d+$"))
async def cc_ask(call: CallbackQuery, state: FSMContext, admin_bot: Bot | None = None) -> None:
    from .extras import _deliver

    _, _, a_s, b_s = call.data.split("_")
    items = await _active_services()
    sa = next((s for s in items if s.id == int(a_s)), None)
    sb = next((s for s in items if s.id == int(b_s)), None)
    if sa is None or sb is None:
        await call.answer()
        return
    await call.answer()
    await _deliver(
        call.message,
        call.from_user.id,
        f"Питання про сумісність процедур: «{sa.name}» і «{sb.name}». Чи можна їх поєднувати і з якою перервою?",
        state,
        admin_bot,
    )


# ======================= 🍂 Сезонна порада =======================


@router.callback_query(F.data == "sc_season")
async def season(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    text, sid = await selfcare.season_info(uid, lang)
    rows = []
    if sid:
        rows.append([_b("📝 Записатися як зазвичай", f"usual_{sid}")])
    rows.append([back_button("sc_menu")])
    await call.answer()
    await safe_edit(call, "🍂 <b>Сезонна порада</b>\n\n" + text, _k(rows))


# ======================= 🎁 Мій рік з нами =======================


@router.callback_query(F.data == "yr_show")
async def year_show(call: CallbackQuery) -> None:
    uid = call.from_user.id
    lang = await _lang_of(uid)
    today = today_local()
    st = await yearcard.stats(uid, today.year)
    if st is None and today.month <= 2:
        st = await yearcard.stats(uid, today.year - 1)
    if st is None:
        await call.answer("Тут з'явиться підсумок року після вашого першого візиту 🌿", show_alert=True)
        return
    from shared.brand import brand as brand_name

    async with session_scope() as session:
        client = await get_client(session, uid)
    name = (client.name.split()[0] if client and client.name else "")
    await call.answer()
    brand = await brand_name()
    try:
        data = yearcard.render(st, name, brand, lang)
        await call.message.answer_photo(
            BufferedInputFile(data, "year.png"),
            caption=yearcard.caption(st, name, lang),
            reply_markup=_k([[back_button("sc_menu")]]),
        )
    except Exception:
        logger.exception("Не вдалося створити картку року")
        await call.message.answer(yearcard.caption(st, name, lang), reply_markup=_k([[back_button("sc_menu")]]))


# ======================= 📜 Листування з майстром =======================


async def history_text(uid: int, limit: int = 12) -> str:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ChatMessage)
                .where(ChatMessage.client_telegram_id == uid)
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    if not rows:
        return "Листування поки порожнє. Напишіть майстру, і розмова зʼявиться тут."
    lines = []
    for m in reversed(rows):
        who = "🙋 Ви" if m.direction == "in" else "💬 Майстер"
        label = {"voice": "🎙 ", "lost": "🧳 "}.get(m.kind, "")
        body = esc(m.text if len(m.text) <= 300 else m.text[:300] + "…")
        lines.append(f"<b>{who}</b> · {m.created_at:%d.%m %H:%M}\n{label}{body}")
    return "\n\n".join(lines)


@router.callback_query(F.data == "ms_menu")
async def ms_menu(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(
        call,
        "📜 <b>Листування з майстром</b>\n\n" + await history_text(call.from_user.id),
        _k([[_b("✍️ Написати майстру", "ms_write")], [back_button("sc_menu")]]),
    )


@router.callback_query(F.data == "ms_write")
async def ms_write(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AskStates.TEXT)
    await call.answer()
    await safe_edit(
        call,
        "💬 Напишіть своє питання одним повідомленням (можна й голосовим 🎙), і я передам його майстру. Відповідь прийде сюди.",
        _k([[back_button("ms_menu")]]),
    )


# ======================= 🎙 Голосове повідомлення майстру =======================

MAX_VOICE_SEC = 120


async def _admin_ids() -> list[int]:
    async with session_scope() as session:
        return list(await get_active_admin_ids(session))


@router.message(AskStates.TEXT, F.voice)
async def ask_voice(msg: Message, state: FSMContext, admin_bot: Bot | None = None) -> None:
    from .extras import _ASKED, ASK_LIMIT
    import time as _time

    voice = msg.voice
    if voice.duration and voice.duration > MAX_VOICE_SEC:
        await msg.answer("Голосове задовге. Запишіть, будь ласка, до 2 хвилин або напишіть текстом.")
        return
    uid = msg.from_user.id
    now = _time.monotonic()
    recent = [t for t in _ASKED.get(uid, []) if now - t < 3600]
    if len(recent) >= ASK_LIMIT:
        await state.clear()
        await msg.answer(
            "Ви вже надіслали кілька повідомлень за останню годину. Майстер відповість, щойно зможе 🌿",
            reply_markup=main_reply_kb(),
        )
        return
    async with session_scope() as session:
        client = await get_client(session, uid)
    ids = await _admin_ids()
    if admin_bot is None or not ids or client is None:
        await msg.answer("Зараз не вдалося передати повідомлення. Спробуйте пізніше або напишіть текстом.", reply_markup=main_reply_kb())
        await state.clear()
        return
    buf = await msg.bot.download(voice)
    raw = buf.read() if buf else b""
    if not raw:
        await msg.answer("Не вдалося отримати голосове. Спробуйте ще раз.")
        return
    _ASKED[uid] = [*recent, now]
    markup = _k([[_b("✍️ Відповісти", f"ansr_{uid}")]])
    sent = False
    for admin_id in ids:
        try:
            await admin_bot.send_voice(
                admin_id,
                BufferedInputFile(raw, "voice.ogg"),
                caption=f"🎙 <b>Голосове від клієнта</b>\n{esc(client.name)}, {esc(client.phone)} · {voice.duration} с",
                reply_markup=markup,
            )
            sent = True
        except TelegramAPIError:
            logger.warning("Не вдалося передати голосове адміну %s", admin_id)
    await state.clear()
    if sent:
        try:
            async with session_scope() as session:
                session.add(ChatMessage(client_telegram_id=uid, direction="in", kind="voice", text=f"Голосове повідомлення, {voice.duration} с"))
        except Exception:
            logger.exception("Не збережено запис про голосове")
    reply = "Передала голосове майстру 🌿 Відповідь прийде сюди." if sent else "Не вдалося передати. Спробуйте пізніше."
    if sent:
        reply = await _with_eta(reply)
    await msg.answer(reply, reply_markup=main_reply_kb())


async def _with_eta(reply: str) -> str:
    try:
        from shared.autoreply import off_hours_note
        from shared.replyeta import eta_note

        note = await off_hours_note() or await eta_note()
        if note:
            reply += "\n\n" + note
    except Exception:
        logger.exception("Орієнтир часу відповіді не сформовано")
    return reply


# ======================= 🧳 Забута річ =======================


@router.callback_query(F.data == "lost_start")
async def lost_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(LostStates.TEXT)
    await call.answer()
    await safe_edit(
        call,
        "🧳 Що саме ви забули, і коли були в салоні? Напишіть коротко, я передам майстру.",
        _k([[back_button("sc_menu")]]),
    )


@router.message(LostStates.TEXT, F.text)
async def lost_send(msg: Message, state: FSMContext, admin_bot: Bot | None = None) -> None:
    from shared import namefilter

    from .extras import _ASKED, ASK_LIMIT
    import time as _time

    text = (msg.text or "").strip()
    if len(text) < 3:
        await msg.answer("Напишіть трохи докладніше, що саме забули.")
        return
    if namefilter.has_obscene(text):
        await msg.answer(namefilter.TEXT_REJECT)
        return
    uid = msg.from_user.id
    now = _time.monotonic()
    recent = [t for t in _ASKED.get(uid, []) if now - t < 3600]
    if len(recent) >= ASK_LIMIT:
        await state.clear()
        await msg.answer("Ви вже надіслали кілька повідомлень за останню годину. Майстер відповість, щойно зможе 🌿", reply_markup=main_reply_kb())
        return
    async with session_scope() as session:
        client = await get_client(session, uid)
    ids = await _admin_ids()
    await state.clear()
    if admin_bot is None or not ids or client is None:
        await msg.answer("Зараз не вдалося передати повідомлення. Зателефонуйте майстру.", reply_markup=main_reply_kb())
        return
    _ASKED[uid] = [*recent, now]
    markup = _k([[_b("✍️ Відповісти", f"ansr_{uid}")]])
    sent = False
    for admin_id in ids:
        try:
            await admin_bot.send_message(
                admin_id,
                f"🧳 <b>Клієнт шукає забуту річ</b>\n{esc(client.name)}, {esc(client.phone)}\n\n{esc(text[:700])}",
                reply_markup=markup,
            )
            sent = True
        except TelegramAPIError:
            logger.warning("Не вдалося передати «забуту річ» адміну %s", admin_id)
    if sent:
        try:
            async with session_scope() as session:
                session.add(ChatMessage(client_telegram_id=uid, direction="in", kind="lost", text=text[:700]))
        except Exception:
            logger.exception("Не збережено запис про забуту річ")
    reply = "Передала майстру 🌿 Вона перевірить і напише вам." if sent else "Не вдалося передати. Спробуйте пізніше."
    if sent:
        reply = await _with_eta(reply)
    await msg.answer(reply, reply_markup=main_reply_kb())
