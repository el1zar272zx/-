# client_bot/handlers/more.py — вибір мови, обране, порівняння, вдвох, прогрес, фото зони,
# «Корисне» (поради, вправи, словничок, тест шкіри), ідеї послуг, календар, простий режим, продовження запису
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import func, select

from shared import library, skintest
from shared.database import session_scope
from shared.i18n import PENDING, set_cached_lang, set_cached_simple
from shared.models import (
    Booking,
    BookingStatus,
    ClientPhoto,
    Favorite,
    Service,
    Suggestion,
    SuggestionVote,
)
from shared.pricing import price_plain
from shared.services import get_booking, get_client, get_service, set_client_field
from shared.tg import safe_edit
from shared.utils import esc, fmt_date_long, parse_cb_int, today_local

from ..keyboards import BTN_USEFUL, T, back_button, main_reply_kb, service_details_kb
from ..states import SuggestStates, ZoneStates

logger = logging.getLogger(__name__)
router = Router(name="client_more")


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _k(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- Вибір мови ----------

LANG_KB = _k([[_b("🇺🇦 Українська", "setlang_uk"), _b("🇷🇺 Русский", "setlang_ru")]])


@router.callback_query(F.data.in_({"setlang_uk", "setlang_ru"}))
async def set_language(call: CallbackQuery, state: FSMContext) -> None:
    lang = "ru" if call.data.endswith("ru") else "uk"
    uid = call.from_user.id
    async with session_scope() as session:
        client = await get_client(session, uid)
        if client is not None:
            await set_client_field(session, uid, lang=lang)
    if client is None:
        PENDING[uid] = lang
    set_cached_lang(uid, lang)
    await call.answer("Язык: русский" if lang == "ru" else "Мова: українська")
    try:
        await call.message.delete()
    except Exception:
        pass
    from .start import _welcome

    await _welcome(call.message, uid)


# ---------- Обрані послуги ----------


@router.callback_query(F.data.regexp(r"^fav_\d+$"))
async def fav_toggle(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "fav_")
    uid = call.from_user.id
    if sid is None:
        await call.answer()
        return
    async with session_scope() as session:
        svc = await session.get(Service, sid)
        if svc is None:
            await call.answer()
            return
        row = (
            await session.execute(
                select(Favorite).where(Favorite.client_telegram_id == uid, Favorite.service_id == sid)
            )
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
            on = False
        else:
            session.add(Favorite(client_telegram_id=uid, service_id=sid))
            on = True
        category_id = svc.category_id
    from shared.works import count_works

    async with session_scope() as session:
        works_n = await count_works(session, sid)
    await call.answer("⭐ Додано в обране" if on else "Прибрано з обраного")
    try:
        await call.message.edit_reply_markup(
            reply_markup=service_details_kb(sid, category_id, works_n, fav=on)
        )
    except Exception:
        pass


@router.callback_query(F.data == "favs")
async def favs_list(call: CallbackQuery) -> None:
    uid = call.from_user.id
    async with session_scope() as session:
        rows = (
            await session.execute(select(Favorite).where(Favorite.client_telegram_id == uid).order_by(Favorite.id))
        ).scalars().all()
        services = [s for s in [await session.get(Service, r.service_id) for r in rows] if s and s.is_active]
    if not services:
        await call.answer("Поки порожньо. Натисніть ⭐ у картці послуги.", show_alert=True)
        return
    await call.answer()
    buttons = [[_b(f"{s.name} · {s.duration_minutes} хв · {price_plain(s)}", f"view_service_{s.id}")] for s in services]
    buttons.append([back_button("profile_back")])
    await safe_edit(call, "⭐ <b>Ваші обрані послуги</b>", _k(buttons))


# ---------- Порівняння послуг ----------


@router.callback_query(F.data.regexp(r"^cmp_\d+$"))
async def compare_pick(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "cmp_")
    async with session_scope() as session:
        svc = await session.get(Service, sid or 0)
        if svc is None:
            await call.answer()
            return
        others = (
            await session.execute(
                select(Service).where(
                    Service.category_id == svc.category_id, Service.is_active.is_(True), Service.id != svc.id
                )
            )
        ).scalars().all()
    if not others:
        await call.answer("У цій категорії немає іншої послуги для порівняння.", show_alert=True)
        return
    await call.answer()
    buttons = [[_b(o.name[:50], f"cmpv_{svc.id}_{o.id}")] for o in others[:10]]
    buttons.append([back_button(f"view_service_{svc.id}")])
    await safe_edit(call, f"⚖️ З чим порівняти <b>{esc(svc.name)}</b>?", _k(buttons))


def _short(text: str | None, n: int = 220) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


@router.callback_query(F.data.regexp(r"^cmpv_\d+_\d+$"))
async def compare_view(call: CallbackQuery) -> None:
    _, a, b = call.data.split("_")
    async with session_scope() as session:
        sa, sb = await session.get(Service, int(a)), await session.get(Service, int(b))
    if sa is None or sb is None:
        await call.answer()
        return
    await call.answer()
    lines = ["⚖️ <b>Порівняння</b>", ""]
    for s in (sa, sb):
        lines += [
            f"✨ <b>{esc(s.name)}</b>",
            f"⏱ {s.duration_minutes} хв · 💰 {price_plain(s)}",
            esc(_short(s.description)) or "Опис ще не додано.",
            "",
        ]
    await safe_edit(
        call,
        "\n".join(lines).strip(),
        _k(
            [
                [_b(f"📝 Записатися: {sa.name[:25]}", f"view_book_{sa.id}")],
                [_b(f"📝 Записатися: {sb.name[:25]}", f"view_book_{sb.id}")],
                [back_button(f"cmp_{sa.id}")],
            ]
        ),
    )


# ---------- Прийти вдвох ----------


@router.callback_query(F.data == "cart_pair")
async def cart_pair(call: CallbackQuery, state: FSMContext) -> None:
    from .booking import MAX_SERVICES, _show_cart

    data = await state.get_data()
    ids = list(data.get("service_ids", []))
    if not ids:
        await call.answer()
        return
    if data.get("pair"):
        await state.update_data(service_ids=ids[: len(ids) // 2], pair=False)
    else:
        if len(ids) > MAX_SERVICES:
            await call.answer("Забагато послуг для запису вдвох.", show_alert=True)
            return
        await state.update_data(service_ids=ids + ids, pair=True)
    await _show_cart(call, state)


# ---------- Мій прогрес (фото до/після за згодою) ----------

KIND_UA = {"before": "До", "after": "Після", "other": "Фото"}


def _consent_kb(on: bool) -> InlineKeyboardMarkup:
    return _k(
        [
            [_b("🚫 Забрати згоду" if on else "✅ Дозволяю зберігати фото", "consent_off" if on else "consent_on")],
            [back_button("profile_back")],
        ]
    )


@router.callback_query(F.data == "prog")
async def progress(call: CallbackQuery) -> None:
    uid = call.from_user.id
    async with session_scope() as session:
        client = await get_client(session, uid)
        photos = []
        if client is not None:
            photos = (
                await session.execute(select(ClientPhoto).where(ClientPhoto.client_id == client.id).order_by(ClientPhoto.id))
            ).scalars().all()
    if client is None:
        await call.answer()
        return
    await call.answer()
    if not client.photo_consent:
        await call.message.answer(
            "📈 <b>Мій прогрес</b>\nТут зберігаються ваші фото до і після процедур, щоб ви бачили результат. "
            "Фото робить майстер лише за вашою згодою, і бачите їх тільки ви та майстер. "
            "Згоду можна забрати будь-коли.",
            reply_markup=_consent_kb(False),
        )
        return
    if not photos:
        await call.message.answer(
            "📈 Поки фото немає. Майстер додасть їх після процедури, і ви побачите прогрес тут.",
            reply_markup=_consent_kb(True),
        )
        return
    try:
        if len(photos) == 1:
            p = photos[0]
            await call.message.answer_photo(
                BufferedInputFile(p.data, "p.jpg"), caption=f"{KIND_UA.get(p.kind, 'Фото')} · {p.created_at:%d.%m.%Y}"
            )
        else:
            media = [
                InputMediaPhoto(
                    media=BufferedInputFile(p.data, f"p{i}.jpg"),
                    caption=f"{KIND_UA.get(p.kind, 'Фото')} · {p.created_at:%d.%m.%Y}",
                )
                for i, p in enumerate(photos[-10:])
            ]
            await call.message.answer_media_group(media)
    except Exception:
        logger.exception("Не вдалося надіслати фото прогресу")
    await call.message.answer("📈 Ваш прогрес. Згоду на фото можна забрати тут.", reply_markup=_consent_kb(True))


@router.callback_query(F.data.in_({"consent_on", "consent_off"}))
async def consent_toggle(call: CallbackQuery) -> None:
    on = call.data == "consent_on"
    async with session_scope() as session:
        await set_client_field(session, call.from_user.id, photo_consent=on)
    await call.answer("Згоду дано ✅" if on else "Згоду знято")
    await safe_edit(
        call,
        "📈 Дякуємо! Тепер майстер може зберігати фото до/після для вас." if on
        else "Згоду знято. Нові фото зберігатися не будуть.",
        _consent_kb(on),
    )


# ---------- Фото зони перед візитом (за бажанням) ----------


async def _zone_ask(call: CallbackQuery, state: FSMContext, booking_id: int) -> None:
    await state.set_state(ZoneStates.PHOTO)
    await state.update_data(zone_booking=booking_id)
    await safe_edit(
        call,
        "📷 Надішліть фото зони, яка вас турбує (як фото, не файлом). Його побачить лише майстер, "
        "і воно допоможе підготуватися до візиту. Це необовʼязково.",
        _k([[_b("Пропустити", f"myb_{booking_id}")]]),
    )


@router.callback_query(F.data.regexp(r"^zone_\d+$"))
async def zone_start(call: CallbackQuery, state: FSMContext) -> None:
    bid = parse_cb_int(call.data, "zone_")
    async with session_scope() as session:
        booking = await get_booking(session, bid or 0)
        client = await get_client(session, call.from_user.id)
    if (
        booking is None
        or client is None
        or booking.client_telegram_id != call.from_user.id
        or booking.date < today_local()
        or booking.status not in (BookingStatus.PENDING, BookingStatus.CONFIRMED)
    ):
        await call.answer("Додати фото можна до майбутнього запису.", show_alert=True)
        return
    await call.answer()
    if not client.photo_consent:
        await safe_edit(
            call,
            "📷 Щоб зберегти ваше фото, потрібна згода. Його побачить лише майстер. Дозволяєте?",
            _k([[_b("✅ Так, дозволяю", f"zoneok_{booking.id}")], [_b("Не зараз", f"myb_{booking.id}")]]),
        )
        return
    await _zone_ask(call, state, booking.id)


@router.callback_query(F.data.regexp(r"^zoneok_\d+$"))
async def zone_consent(call: CallbackQuery, state: FSMContext) -> None:
    bid = parse_cb_int(call.data, "zoneok_") or 0
    async with session_scope() as session:
        await set_client_field(session, call.from_user.id, photo_consent=True)
    await call.answer("Дякуємо ✅")
    await _zone_ask(call, state, bid)


@router.message(ZoneStates.PHOTO, F.photo)
async def zone_save(msg: Message, state: FSMContext, admin_bot: Bot | None = None) -> None:
    from shared.works import compress

    data = await state.get_data()
    bid = data.get("zone_booking")
    buf = await msg.bot.download(msg.photo[-1])
    raw = buf.read() if buf else b""
    if not raw or bid is None:
        await msg.answer("Не вдалося завантажити фото. Спробуйте ще раз.")
        return
    jpeg = compress(raw)
    async with session_scope() as session:
        client = await get_client(session, msg.from_user.id)
        if client is None:
            await state.clear()
            return
        session.add(ClientPhoto(client_id=client.id, data=jpeg, kind="before", note=f"до візиту #{bid}"))
        name, phone = client.name, client.phone
    await state.clear()
    await msg.answer("Фото додано ✅ Майстер побачить його перед візитом.", reply_markup=main_reply_kb())
    if admin_bot is not None:
        try:
            from shared.services import get_active_admin_ids

            async with session_scope() as session:
                ids = await get_active_admin_ids(session)
            for admin_id in ids:
                try:
                    await admin_bot.send_photo(
                        admin_id,
                        BufferedInputFile(jpeg, "zone.jpg"),
                        caption=f"📷 Фото зони до запису #{bid}\n{esc(name)}, {esc(phone)}",
                    )
                except Exception:
                    logger.warning("Не вдалося надіслати фото зони адміну %s", admin_id)
        except Exception:
            logger.exception("Фото зони не передано майстру")


@router.message(ZoneStates.PHOTO)
async def zone_wrong(msg: Message) -> None:
    await msg.answer("Надішліть саме фото (не файлом) або натисніть «Пропустити».")


# ---------- Календар телефону ----------


@router.callback_query(F.data.regexp(r"^ics_\d+$"))
async def booking_ics(call: CallbackQuery) -> None:
    from shared.calendar_utils import generate_ics_for_bookings

    bid = parse_cb_int(call.data, "ics_")
    async with session_scope() as session:
        booking = await get_booking(session, bid or 0)
    if booking is None or booking.client_telegram_id != call.from_user.id:
        await call.answer("Запис не знайдено.", show_alert=True)
        return
    await call.answer()
    try:
        data = generate_ics_for_bookings([booking], for_admin=False)
        await call.message.answer_document(
            BufferedInputFile(data, filename=f"zapys_{booking.id}.ics"),
            caption=f"📆 {fmt_date_long(booking.date)}. Відкрийте файл, і запис з'явиться в календарі телефону.",
        )
    except Exception:
        logger.exception("Не вдалося надіслати файл календаря")
        await call.message.answer("Не вдалося підготувати файл. Спробуйте ще раз трохи пізніше.")


# ---------- Простий режим і поради тижня ----------


@router.callback_query(F.data == "profile_simple")
async def toggle_simple(call: CallbackQuery) -> None:
    uid = call.from_user.id
    async with session_scope() as session:
        client = await get_client(session, uid)
        if client is None:
            await call.answer()
            return
        new = not client.simple
        await set_client_field(session, uid, simple=new)
    set_cached_simple(uid, new)
    await call.answer("Простий режим увімкнено" if new else "Простий режим вимкнено")
    await call.message.answer(
        "🔎 Тепер меню простіше: лише найнеобхідніше." if new else "Звичайне меню повернуто.",
        reply_markup=main_reply_kb(),
    )


@router.callback_query(F.data == "profile_tips")
async def toggle_tips(call: CallbackQuery) -> None:
    uid = call.from_user.id
    async with session_scope() as session:
        client = await get_client(session, uid)
        if client is None:
            await call.answer()
            return
        new = not client.tips_off
        await set_client_field(session, uid, tips_off=new)
    await call.answer(
        "Поради тижня вимкнено. Увімкнути знову можна тут же." if new else "Поради тижня увімкнено 💡",
        show_alert=True,
    )


@router.callback_query(F.data == "tips_stop")
async def tips_stop(call: CallbackQuery) -> None:
    uid = call.from_user.id
    async with session_scope() as session:
        await set_client_field(session, uid, tips_off=True)
    await call.answer("Добре, більше поради не надсилатимемо 🌿", show_alert=True)


# ---------- Корисне: поради, вправи, словничок, тест шкіри, ідеї ----------


def _useful_kb() -> InlineKeyboardMarkup:
    return _k(
        [
            [_b("🤸 Вправи й домашній догляд", "use_list_ex")],
            [_b("📖 Словничок термінів", "use_list_term")],
            [_b("💡 Поради", "use_list_tip")],
            [_b("🧴 Тест типу шкіри", "skin_start")],
            [_b("💡 Запропонувати послугу", "sug_new"), _b("🗳 Ідеї клієнтів", "sug_list")],
        ]
    )


USEFUL_TEXT = "📚 <b>Корисне</b>\nКоротко й по ділу: вправи, терміни, поради та тест шкіри."


@router.message(F.text.in_(T(BTN_USEFUL)))
async def useful_menu(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(USEFUL_TEXT, reply_markup=_useful_kb())


@router.callback_query(F.data == "use_menu")
async def useful_menu_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, USEFUL_TEXT, _useful_kb())


@router.callback_query(F.data.regexp(r"^use_list_(tip|ex|term)$"))
async def useful_list(call: CallbackQuery) -> None:
    kind = call.data.removeprefix("use_list_")
    async with session_scope() as session:
        items = await library.items(session, kind)
    if not items:
        await call.answer("Поки порожньо.", show_alert=True)
        return
    await call.answer()
    buttons = [[_b(i.title[:55], f"use_item_{i.id}")] for i in items[:30]]
    buttons.append([back_button("use_menu")])
    await safe_edit(call, library.KINDS[kind], _k(buttons))


@router.callback_query(F.data.regexp(r"^use_item_\d+$"))
async def useful_item(call: CallbackQuery) -> None:
    iid = parse_cb_int(call.data, "use_item_")
    async with session_scope() as session:
        item = await library.get(session, iid or 0)
    if item is None or not item.active:
        await call.answer("Не знайдено.", show_alert=True)
        return
    await call.answer()
    await safe_edit(
        call,
        f"<b>{esc(item.title)}</b>\n\n{esc(item.text)}",
        _k([[back_button(f"use_list_{item.kind}")]]),
    )


# --- Тест типу шкіри ---


def _skin_kb(q: int) -> InlineKeyboardMarkup:
    opts = skintest.QUESTIONS[q][1]
    rows = [[_b(text, f"sk_{q}_{i}")] for i, (text, _t) in enumerate(opts)]
    rows.append([back_button("use_menu")])
    return _k(rows)


@router.callback_query(F.data == "skin_start")
async def skin_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(skin=[])
    await call.answer()
    await safe_edit(call, f"🧴 <b>Питання 1 з {len(skintest.QUESTIONS)}</b>\n{skintest.QUESTIONS[0][0]}", _skin_kb(0))


@router.callback_query(F.data.regexp(r"^sk_\d+_\d+$"))
async def skin_answer(call: CallbackQuery, state: FSMContext) -> None:
    _, q, i = call.data.split("_")
    q, i = int(q), int(i)
    data = await state.get_data()
    answers = list(data.get("skin", []))
    if q != len(answers) or q >= len(skintest.QUESTIONS) or i >= len(skintest.QUESTIONS[q][1]):
        await call.answer("Почнемо спочатку", show_alert=False)
        await state.update_data(skin=[])
        await safe_edit(call, f"🧴 <b>Питання 1 з {len(skintest.QUESTIONS)}</b>\n{skintest.QUESTIONS[0][0]}", _skin_kb(0))
        return
    answers.append(skintest.QUESTIONS[q][1][i][1])
    await state.update_data(skin=answers)
    await call.answer()
    if q + 1 < len(skintest.QUESTIONS):
        await safe_edit(
            call,
            f"🧴 <b>Питання {q + 2} з {len(skintest.QUESTIONS)}</b>\n{skintest.QUESTIONS[q + 1][0]}",
            _skin_kb(q + 1),
        )
        return
    kind = skintest.result(answers)
    title, advice, keys = skintest.RESULTS[kind]
    async with session_scope() as session:
        services = (await session.execute(select(Service).where(Service.is_active.is_(True)))).scalars().all()
    picks = [s for s in services if any(k in s.name.casefold() for k in keys)][:4]
    text = f"🧴 <b>Ваш тип: {title}</b>\n\n{advice}\n\n<i>Це орієнтир, а не діагноз. Точніше підкаже майстер.</i>"
    if picks:
        text += "\n\nМожуть підійти:"
    buttons = [[_b(f"{s.name[:40]} · {price_plain(s)}", f"view_service_{s.id}")] for s in picks]
    buttons.append([back_button("use_menu")])
    await safe_edit(call, text, _k(buttons))


# --- Ідеї послуг ---


@router.callback_query(F.data == "sug_new")
async def sug_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SuggestStates.TEXT)
    await call.answer()
    await safe_edit(
        call,
        "💡 Напишіть, яку послугу чи процедуру ви хотіли б бачити в салоні (до 200 символів). "
        "Інші клієнти зможуть її підтримати.",
        _k([[back_button("use_menu")]]),
    )


@router.message(SuggestStates.TEXT, F.text)
async def sug_save(msg: Message, state: FSMContext) -> None:
    from shared import namefilter

    text = " ".join(msg.text.split())
    if text in set().union(*(T(b) for b in (BTN_USEFUL,))) or text.startswith("/"):
        return
    if not 4 <= len(text) <= 200:
        await msg.answer("Напишіть коротко, від 4 до 200 символів.")
        return
    if namefilter.has_obscene(text):
        await msg.answer(namefilter.TEXT_REJECT)
        return
    uid = msg.from_user.id
    async with session_scope() as session:
        mine = await session.scalar(
            select(func.count()).select_from(Suggestion).where(Suggestion.client_telegram_id == uid)
        )
        if (mine or 0) >= 5:
            await state.clear()
            await msg.answer("Ви вже запропонували кілька ідей. Дякуємо, майстер їх розгляне 🌿", reply_markup=main_reply_kb())
            return
        s = Suggestion(text=text, client_telegram_id=uid)
        session.add(s)
        await session.flush()
        session.add(SuggestionVote(suggestion_id=s.id, client_telegram_id=uid))
    await state.clear()
    await msg.answer("Дякуємо! Ідею збережено 🌿", reply_markup=main_reply_kb())


async def _sug_view(call: CallbackQuery) -> None:
    uid = call.from_user.id
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Suggestion, func.count(SuggestionVote.id).label("n"))
                .outerjoin(SuggestionVote, SuggestionVote.suggestion_id == Suggestion.id)
                .where(Suggestion.hidden.is_(False))
                .group_by(Suggestion.id)
                .order_by(func.count(SuggestionVote.id).desc(), Suggestion.id.desc())
                .limit(8)
            )
        ).all()
        mine = {
            r for r in (
                await session.execute(select(SuggestionVote.suggestion_id).where(SuggestionVote.client_telegram_id == uid))
            ).scalars().all()
        }
    if not rows:
        await safe_edit(
            call,
            "Ідей поки немає. Станьте першими!",
            _k([[_b("💡 Запропонувати послугу", "sug_new")], [back_button("use_menu")]]),
        )
        return
    lines = ["🗳 <b>Ідеї клієнтів</b>\nНатисніть, щоб підтримати:", ""]
    buttons = []
    for s, n in rows:
        lines.append(f"• {esc(s.text)} — 👍 {n}")
        mark = "✅" if s.id in mine else "👍"
        buttons.append([_b(f"{mark} {s.text[:35]} ({n})", f"sugv_{s.id}")])
    buttons.append([back_button("use_menu")])
    await safe_edit(call, "\n".join(lines), _k(buttons))


@router.callback_query(F.data == "sug_list")
async def sug_list(call: CallbackQuery) -> None:
    await call.answer()
    await _sug_view(call)


@router.callback_query(F.data.regexp(r"^sugv_\d+$"))
async def sug_vote(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "sugv_") or 0
    uid = call.from_user.id
    async with session_scope() as session:
        row = (
            await session.execute(
                select(SuggestionVote).where(SuggestionVote.suggestion_id == sid, SuggestionVote.client_telegram_id == uid)
            )
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
        else:
            session.add(SuggestionVote(suggestion_id=sid, client_telegram_id=uid))
    await call.answer("Голос враховано")
    await _sug_view(call)


# ---------- Продовження незавершеного запису ----------


@router.callback_query(F.data == "draft_go")
async def draft_resume(call: CallbackQuery, state: FSMContext) -> None:
    from shared import drafts

    from .booking import _show_cart, begin_booking

    draft = await drafts.get(call.from_user.id)
    if draft is None:
        await call.answer("Чернетку не знайдено. Почнімо спочатку.")
        await begin_booking(call, state)
        return
    ids = [int(x) for x in draft.service_ids.split(",") if x.strip().isdigit()]
    await state.clear()
    await state.update_data(service_ids=ids, city_id=draft.city_id)
    await _show_cart(call, state)
