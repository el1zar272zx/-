# client_bot/handlers/extras.py — «Як нас знайти», повтор запису, день народження, мова, анкета
from __future__ import annotations

import logging
import re
from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from shared import quiz, salon
from shared.config import settings
from shared.database import session_scope
from shared.i18n import set_cached_lang
from shared.services import (
    get_active_cities,
    get_client,
    get_last_service_id,
    get_service,
    set_client_field,
)
from shared.tg import safe_edit
from shared.utils import esc, today_local

from ..keyboards import (
    T,
    BTN_ASK,
    BTN_FAQ,
    BTN_INFO,
    BTN_QUIZ,
    INTAKE_ITEMS,
    back_only_kb,
    intake_kb,
    main_reply_kb,
    pref_days_kb,
)
from ..states import AskStates, IntakeStates, ProfileStates

logger = logging.getLogger(__name__)
router = Router(name="client_extras")


# ---------- Як нас знайти (адреса і фото редагуються в адмін-боті) ----------


@router.message(F.text.in_(T(BTN_INFO)))
async def salon_info(msg: Message, state: FSMContext) -> None:
    await state.clear()
    address = salon.info("address") or settings.salon_address
    phone = salon.info("phone") or settings.salon_phone
    map_url = salon.info("map_url") or settings.salon_map_url
    lines = ["📍 <b>Як нас знайти</b>", ""]
    if address:
        lines.append(esc(address))
    if phone:
        lines.append(f"Телефон: {esc(phone)}")
    if map_url:
        lines.append(f"Карта: {esc(map_url)}")
    if salon.info("howto"):
        lines.append(f"🚪 {esc(salon.info('howto'))}")
    if salon.info("instagram"):
        lines.append(f"📷 Instagram: {esc(salon.info('instagram'))}")
    if len(lines) == 2:
        lines.append("Адресу ще не вказано. Зателефонуйте майстру або напишіть їй.")
    await msg.answer("\n".join(lines))
    try:
        async with session_scope() as session:
            photos = await salon.list_photos(session)
        if len(photos) == 1:
            p = photos[0]
            await msg.answer_photo(BufferedInputFile(p.data, "salon.jpg"), caption=p.caption or None)
        elif photos:
            media = [
                InputMediaPhoto(
                    media=BufferedInputFile(p.data, f"salon{i}.jpg"), caption=p.caption or None
                )
                for i, p in enumerate(photos[:10])
            ]
            await msg.answer_media_group(media)
    except Exception:
        logger.exception("Не вдалося надіслати фото салону")
    if settings.salon_lat and settings.salon_lon:
        try:
            await msg.answer_location(float(settings.salon_lat), float(settings.salon_lon))
        except Exception:
            logger.warning("Некоректні SALON_LAT/SALON_LON")


# ---------- Часті питання ----------


@router.message(F.text.in_(T(BTN_FAQ)))
async def faq_list(msg: Message, state: FSMContext) -> None:
    await state.clear()
    async with session_scope() as session:
        items = await salon.list_faq(session)
    if not items:
        await msg.answer("Поки що питань немає. Напишіть або зателефонуйте майстру.")
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=i.question[:60], callback_data=f"faq_{i.id}")] for i in items
        ]
    )
    await msg.answer("❓ <b>Часті питання</b>\nОберіть питання:", reply_markup=kb)


@router.callback_query(F.data.startswith("faq_"))
async def faq_show(call: CallbackQuery) -> None:
    try:
        fid = int(call.data.removeprefix("faq_"))
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        item = next((i for i in await salon.list_faq(session) if i.id == fid), None)
    await call.answer()
    if item is None:
        return
    await call.message.answer(f"<b>{esc(item.question)}</b>\n\n{esc(item.answer)}")


# ---------- Спосіб зв'язку та зручні дні ----------


async def _refresh_profile(call: CallbackQuery) -> None:
    from ..keyboards import profile_kb
    from .profile import _profile_text

    text = await _profile_text(call.from_user.id)
    if text:
        await safe_edit(call, text, profile_kb())


@router.callback_query(F.data == "profile_contact")
async def contact_toggle(call: CallbackQuery) -> None:
    async with session_scope() as session:
        client = await get_client(session, call.from_user.id)
        if client is None:
            await call.answer()
            return
        new = "call" if client.contact_pref != "call" else "tg"
        await set_client_field(session, call.from_user.id, contact_pref=new)
    await call.answer(
        "Майстер зателефонує вам, якщо буде потрібно" if new == "call"
        else "Майстер напише вам у Telegram",
        show_alert=True,
    )
    await _refresh_profile(call)


async def _pref_set(telegram_id: int) -> set[int]:
    async with session_scope() as session:
        client = await get_client(session, telegram_id)
    raw = (client.pref_days if client else "") or ""
    return {int(x) for x in raw.split(",") if x.strip().isdigit()}


@router.callback_query(F.data == "profile_days")
async def days_open(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(
        call,
        "🗓 Оберіть дні тижня, які вам зручні. У календарі запису вони будуть із ⭐.",
        pref_days_kb(await _pref_set(call.from_user.id)),
    )


@router.callback_query(F.data.regexp(r"^pd_[0-6]$"))
async def days_toggle(call: CallbackQuery) -> None:
    i = int(call.data[3:])
    sel = await _pref_set(call.from_user.id)
    sel ^= {i}
    async with session_scope() as session:
        await set_client_field(
            session, call.from_user.id, pref_days=",".join(str(x) for x in sorted(sel))
        )
    await call.answer()
    await safe_edit(call, call.message.text or "", pref_days_kb(sel))


@router.callback_query(F.data == "profile_quiet")
async def quiet_open(call: CallbackQuery) -> None:
    from ..keyboards import quiet_kb

    async with session_scope() as session:
        client = await get_client(session, call.from_user.id)
    await call.answer()
    await safe_edit(
        call,
        "🌙 <b>Тихі години</b>\nУ цей час я не надсилатиму нагадування й поради. "
        "Вони прийдуть, щойно тихі години закінчаться. Нагадування за 2 години до візиту "
        "надсилаються завжди.",
        quiet_kb(client.quiet if client else None),
    )


@router.callback_query(F.data.regexp(r"^qh_(off|\d+-\d+)$"))
async def quiet_set(call: CallbackQuery) -> None:
    from shared.quiet import OPTIONS

    from ..keyboards import quiet_kb

    key = call.data[3:]
    if key not in OPTIONS:
        await call.answer()
        return
    value = OPTIONS[key][1]
    async with session_scope() as session:
        await set_client_field(session, call.from_user.id, quiet=value)
    await call.answer("Збережено ✅")
    await safe_edit(call, call.message.html_text or "🌙 Тихі години", quiet_kb(value))


# ---------- Повторити минулий запис ----------


@router.callback_query(F.data == "repeat_last")
async def repeat_last(call: CallbackQuery, state: FSMContext) -> None:
    from .booking import _show_dates, begin_booking

    async with session_scope() as session:
        service_id = await get_last_service_id(session, call.from_user.id)
        service = await get_service(session, service_id) if service_id else None
        cities = await get_active_cities(session)
        client = await get_client(session, call.from_user.id)
    if service is None or not service.is_active or not cities:
        await call.answer("Минулого запису не знайдено. Оберіть послугу заново.", show_alert=True)
        await begin_booking(call, state)
        return
    if client is not None and client.intake is None:
        await begin_booking(call, state)  # спочатку коротка анкета
        return
    await state.clear()
    await state.update_data(city_id=cities[0].id, service_ids=[service.id])
    await _show_dates(call, state)


@router.callback_query(F.data.regexp(r"^usual_\d+$"))
async def usual_book(call: CallbackQuery, state: FSMContext) -> None:
    from .booking import _show_dates, begin_booking

    sid = int(call.data.removeprefix("usual_"))
    async with session_scope() as session:
        service = await get_service(session, sid)
        cities = await get_active_cities(session)
        client = await get_client(session, call.from_user.id)
    if service is None or not service.is_active or not cities:
        await call.answer("Цієї послуги вже немає. Оберіть іншу.", show_alert=True)
        await begin_booking(call, state)
        return
    if client is not None and client.intake is None:
        await begin_booking(call, state)  # спочатку коротка анкета
        return
    await state.clear()
    await state.update_data(city_id=cities[0].id, service_ids=[service.id])
    await call.answer()
    await _show_dates(call, state, answered=True)


# ---------- Мова ----------


@router.callback_query(F.data == "profile_lang")
async def lang_toggle(call: CallbackQuery) -> None:
    async with session_scope() as session:
        client = await get_client(session, call.from_user.id)
        if client is None:
            await call.answer()
            return
        new = "ru" if client.lang != "ru" else "uk"
        await set_client_field(session, call.from_user.id, lang=new)
    set_cached_lang(call.from_user.id, new)
    await call.answer("Язык: русский" if new == "ru" else "Мова: українська")
    from .profile import _profile_text
    from ..keyboards import profile_kb

    text = await _profile_text(call.from_user.id)
    if text:
        await safe_edit(call, text, profile_kb())


# ---------- День народження ----------

_BDAY = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\s*$")


@router.callback_query(F.data == "profile_bday")
async def bday_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileStates.EDIT_BDAY)
    await call.answer()
    await safe_edit(
        call,
        "Напишіть дату народження у форматі ДД.ММ або ДД.ММ.РРРР. "
        "У цей день я надішлю вітання 🎂",
        back_only_kb("profile_back"),
    )


@router.message(ProfileStates.EDIT_BDAY, F.text)
async def bday_save(msg: Message, state: FSMContext) -> None:
    m = _BDAY.match(msg.text)
    parsed: date | None = None
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else 2000
        if year < 100:
            year += 1900 if year > 30 else 2000
        try:
            parsed = date(year, month, day)
        except ValueError:
            parsed = None
    if parsed is None or parsed > today_local():
        await msg.answer("Не вдалося розпізнати дату. Приклад: 25.03 або 25.03.1990")
        return
    async with session_scope() as session:
        await set_client_field(session, msg.from_user.id, birthday=parsed)
    await state.clear()
    await msg.answer(f"Збережено: {parsed:%d.%m} 🎂", reply_markup=main_reply_kb())


# ---------- Анкета перед першим записом ----------


async def ask_intake(event, state: FSMContext) -> None:
    await state.set_state(IntakeStates.CHOOSING)
    await state.update_data(intake_sel=[])
    text = (
        "Перед першим записом дві хвилини безпеки 🌿\n"
        "Позначте, якщо щось із цього стосується вас (або нічого не позначайте), "
        "і натисніть «Готово». Майстер врахує це під час сеансу."
    )
    if isinstance(event, CallbackQuery):
        await event.answer()
        await safe_edit(event, text, intake_kb([]))
    else:
        await event.answer(text, reply_markup=intake_kb([]))


@router.callback_query(IntakeStates.CHOOSING, F.data.startswith("intk_"))
async def intake_pick(call: CallbackQuery, state: FSMContext) -> None:
    from .booking import _show_categories

    arg = call.data.removeprefix("intk_")
    data = await state.get_data()
    selected: list[int] = list(data.get("intake_sel", []))
    if arg == "done":
        names = [INTAKE_ITEMS[i] for i in sorted(selected)]
        value = (", ".join(names) if names else "нічого не вказано") + f" ({today_local():%d.%m.%Y})"
        async with session_scope() as session:
            await set_client_field(session, call.from_user.id, intake=value)
        await state.set_state(None)
        await call.answer("Дякуємо!")
        await _show_categories(call, state)
        return
    try:
        i = int(arg)
        if not 0 <= i < len(INTAKE_ITEMS):
            raise ValueError
    except ValueError:
        await call.answer()
        return
    selected = [x for x in selected if x != i] if i in selected else [*selected, i]
    await state.update_data(intake_sel=selected)
    await call.answer()
    await safe_edit(call, call.message.text or "", intake_kb(selected))


# ---------- Повторний візит з нагадування ----------


@router.callback_query(F.data.startswith("recall_"))
async def recall_book(call: CallbackQuery, state: FSMContext) -> None:
    from .booking import _show_dates, begin_booking

    try:
        sid = int(call.data.removeprefix("recall_"))
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        service = await get_service(session, sid)
        cities = await get_active_cities(session)
        client = await get_client(session, call.from_user.id)
    if service is None or not service.is_active or not cities:
        await call.answer("Цієї послуги вже немає. Оберіть іншу.", show_alert=True)
        await begin_booking(call, state)
        return
    if client is not None and client.intake is None:
        await begin_booking(call, state)
        return
    await state.clear()
    await state.update_data(city_id=cities[0].id, service_ids=[service.id])
    await _show_dates(call, state)


# ---------- Опитування про самопочуття ----------


@router.callback_query(F.data.startswith("fu_ok_"))
async def followup_ok(call: CallbackQuery) -> None:
    await call.answer("Дякуємо!")
    await safe_edit(call, "Дуже рада чути 💛 Гарного дня!")


@router.callback_query(F.data.startswith("fu_bad_"))
async def followup_bad(call: CallbackQuery, admin_bot: Bot | None = None) -> None:
    from admin_bot.notify import notify_followup_issue
    from shared.services import get_booking

    try:
        bid = int(call.data.removeprefix("fu_bad_"))
    except ValueError:
        await call.answer()
        return
    async with session_scope() as session:
        booking = await get_booking(session, bid)
    if booking is None or booking.client_telegram_id != call.from_user.id:
        await call.answer()
        return
    await call.answer()
    await safe_edit(
        call,
        "Дякую, що повідомили. Я передала майстру, вона зв'яжеться з вами найближчим часом. "
        "Якщо стан різко погіршується, зверніться до лікаря.",
    )
    try:
        await notify_followup_issue(admin_bot, booking)
    except Exception:
        logger.exception("Не вдалося повідомити майстра про скаргу після процедури")


# ---------- Що мені підійде ----------


def _quiz_areas_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=a[0][:60], callback_data=f"qz_a_{i}")] for i, a in enumerate(quiz.AREAS)]
    rows.append([InlineKeyboardButton(text="⚠️ Гострий біль, травма або хвороба", callback_data="qz_red")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_(T(BTN_QUIZ)))
async def quiz_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer(
        "🧭 <b>Що мені підійде</b>\nЩо вас найбільше турбує чи цікавить?",
        reply_markup=_quiz_areas_kb(),
    )


@router.callback_query(F.data == "qz_red")
async def quiz_red(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(call, quiz.RED_FLAGS, _quiz_areas_kb())


@router.callback_query(F.data.regexp(r"^qz_a_\d+$"))
async def quiz_area(call: CallbackQuery) -> None:
    i = int(call.data.removeprefix("qz_a_"))
    if not 0 <= i < len(quiz.AREAS):
        await call.answer()
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="До 45 хвилин", callback_data=f"qz_t_{i}_s")],
            [InlineKeyboardButton(text="Година й більше", callback_data=f"qz_t_{i}_l")],
            [InlineKeyboardButton(text="Не важливо", callback_data=f"qz_t_{i}_a")],
        ]
    )
    await call.answer()
    await safe_edit(call, "Скільки часу ви готові виділити на візит?", kb)


@router.callback_query(F.data.regexp(r"^qz_t_\d+_[sla]$"))
async def quiz_result(call: CallbackQuery) -> None:
    _, _, idx, mode = call.data.split("_")
    i = int(idx)
    if not 0 <= i < len(quiz.AREAS):
        await call.answer()
        return
    label, keywords, tip = quiz.AREAS[i]
    from sqlalchemy import select

    from shared.models import Service

    async with session_scope() as session:
        services = (
            await session.execute(select(Service).where(Service.is_active.is_(True)))
        ).scalars().all()
    def _rank(s) -> int:
        name = s.name.casefold()
        for n, k in enumerate(keywords):
            if k in name:
                return n
        return -1

    found = [(r, s) for s in services if (r := _rank(s)) >= 0]

    def _fits(s) -> bool:
        return (
            mode == "a"
            or (mode == "s" and s.duration_minutes <= 45)
            or (mode == "l" and s.duration_minutes >= 60)
        )

    # спочатку підходящі за часом, далі за релевантністю; довші першими для «година й більше»
    def _key(item):
        r, s = item
        dur = -s.duration_minutes if mode == "l" else s.duration_minutes
        return (0 if _fits(s) else 1, r, dur)

    found.sort(key=_key)
    shown = [s for _, s in found][:8]
    fit_count = sum(1 for _, s in found[:8] if _fits(s))
    await call.answer()
    if not shown:
        phone = salon.info("phone")
        await safe_edit(
            call,
            f"<b>{esc(label)}</b>\n{esc(tip)}\n\nПідходящої послуги в списку я не знайшла. "
            "Напишіть або зателефонуйте майстру, вона підкаже"
            + (f": {esc(phone)}." if phone else "."),
        )
        return
    def _btn(s) -> str:
        price = f" · {s.price} грн" if s.price else ""
        return f"{s.name[:34]} · {s.duration_minutes} хв{price}"

    rows = [
        [InlineKeyboardButton(text=_btn(s), callback_data=f"view_service_{s.id}")]
        for s in shown
    ]
    if mode == "a":
        intro = "Ось що можна розглянути:"
    elif fit_count == 0:
        intro = "На обраний час точних варіантів немає. Ось що є за іншою тривалістю:"
    elif fit_count < len(shown):
        intro = "Ось що підходить за часом, а нижче ще кілька варіантів іншої тривалості:"
    else:
        intro = "Ось що підходить:"
    await safe_edit(
        call,
        f"<b>{esc(label)}</b>\n{esc(tip)}\n\n{intro}\n\n"
        f"{quiz.RED_FLAGS}",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )


# ---------- Запитати майстра ----------


@router.message(F.text.in_(T(BTN_ASK)))
async def ask_start(msg: Message, state: FSMContext) -> None:
    await state.set_state(AskStates.TEXT)
    await msg.answer(
        "💬 Напишіть своє питання одним повідомленням (можна й голосовим 🎙), і я передам його майстру. "
        "Відповідь прийде вам сюди.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🧳 Забули річ у салоні?", callback_data="lost_start")],
                [InlineKeyboardButton(text="📜 Історія листування", callback_data="ms_menu")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main_menu")],
            ]
        ),
    )


_ASKED: dict[int, list[float]] = {}
ASK_LIMIT = 3  # питань на годину від одного клієнта


@router.message(AskStates.TEXT, F.text)
async def ask_send(msg: Message, state: FSMContext, admin_bot: Bot | None = None) -> None:
    from aiogram.exceptions import TelegramAPIError

    from shared.services import get_active_admin_ids

    text = msg.text.strip()
    if len(text) < 3:
        await msg.answer("Напишіть, будь ласка, питання повніше.")
        return
    from shared import namefilter

    if namefilter.has_obscene(text):
        from .profile import _flag_abuse

        await _flag_abuse(msg, admin_bot)
        await msg.answer(namefilter.TEXT_REJECT)
        return
    data = await state.get_data()
    if not data.get("ask_force"):
        try:
            hits = await _faq_hits(text)
        except Exception:
            logger.exception("Пошук у FAQ не вдався")
            hits = []
        if hits:
            await state.update_data(ask_text=text)
            rows = [[InlineKeyboardButton(text=q[:55], callback_data=f"faqs_{fid}")] for fid, q in hits]
            rows.append([InlineKeyboardButton(text="✉️ Все одно написати майстру", callback_data="ask_force")])
            await msg.answer(
                "🔎 Можливо, відповідь уже є тут. Оберіть питання або надішліть своє майстру:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            )
            return
    await _deliver(msg, msg.from_user.id, text, state, admin_bot)


async def _faq_hits(text: str) -> list[tuple[int, str]]:
    from shared import faqsearch

    async with session_scope() as session:
        items = await salon.list_faq(session)
    ids = faqsearch.best(text, [(i.id, i.question, i.answer) for i in items])
    by_id = {i.id: i.question for i in items}
    return [(fid, by_id[fid]) for fid in ids]


@router.callback_query(F.data.regexp(r"^faqs_\d+$"))
async def faq_search_show(call: CallbackQuery) -> None:
    fid = int(call.data.removeprefix("faqs_"))
    async with session_scope() as session:
        item = next((i for i in await salon.list_faq(session) if i.id == fid), None)
    await call.answer()
    if item is None:
        return
    await call.message.answer(
        f"<b>{esc(item.question)}</b>\n\n{esc(item.answer)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Це допомогло", callback_data="ask_done")],
                [InlineKeyboardButton(text="✉️ Все одно написати майстру", callback_data="ask_force")],
            ]
        ),
    )


@router.callback_query(F.data == "ask_done")
async def ask_done(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Рада, що допомогло 🌿")
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@router.callback_query(F.data == "ask_force")
async def ask_force(call: CallbackQuery, state: FSMContext, admin_bot: Bot | None = None) -> None:
    text = (await state.get_data()).get("ask_text")
    await call.answer()
    if not text:
        await call.message.answer("Напишіть питання ще раз, будь ласка.")
        await state.set_state(AskStates.TEXT)
        return
    await _deliver(call.message, call.from_user.id, text, state, admin_bot)


async def _deliver(msg: Message, user_id: int, text: str, state: FSMContext, admin_bot: Bot | None) -> None:
    """Передає питання майстру (з лімітом на годину)."""
    from aiogram.exceptions import TelegramAPIError

    from shared.services import get_active_admin_ids

    import time as _time

    now = _time.monotonic()
    recent = [t for t in _ASKED.get(user_id, []) if now - t < 3600]
    if len(recent) >= ASK_LIMIT:
        await state.clear()
        await msg.answer(
            "Ви вже надіслали кілька питань за останню годину. Майстер відповість, щойно зможе 🌿",
            reply_markup=main_reply_kb(),
        )
        return
    _ASKED[user_id] = [*recent, now]
    try:
        from shared.models import ChatMessage, ClientQuestion

        async with session_scope() as session:
            session.add(ClientQuestion(client_telegram_id=user_id, text=text[:1000]))
            session.add(ChatMessage(client_telegram_id=user_id, direction="in", kind="text", text=text[:1000]))
    except Exception:
        logger.exception("Питання не збережено для статистики")
    await state.clear()
    async with session_scope() as session:
        client = await get_client(session, user_id)
        admin_ids = await get_active_admin_ids(session)
    if admin_bot is None or not admin_ids or client is None:
        phone = salon.info("phone")
        await msg.answer(
            "Зараз не вдалося передати питання. Зателефонуйте майстру" + (f": {esc(phone)}" if phone else "."),
            reply_markup=main_reply_kb(),
        )
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✍️ Відповісти", callback_data=f"ansr_{user_id}")]]
    )
    sent = False
    for admin_id in admin_ids:
        try:
            await admin_bot.send_message(
                admin_id,
                f"💬 <b>Питання від клієнта</b>\n{esc(client.name)}, {esc(client.phone)}\n\n{esc(text)}",
                reply_markup=markup,
            )
            sent = True
        except TelegramAPIError:
            logger.warning("Не вдалося передати питання адміну %s", admin_id)
    reply = "Передала майстру 🌿 Відповідь прийде сюди." if sent else "Не вдалося передати питання. Спробуйте пізніше."
    if sent:
        try:
            from shared.autoreply import off_hours_note
            from shared.replyeta import eta_note

            note = await off_hours_note() or await eta_note()
            if note:
                reply += "\n\n" + note
        except Exception:
            logger.exception("Автовідповідь не сформовано")
    await msg.answer(reply, reply_markup=main_reply_kb())


# ---------- Команди меню Telegram ----------


@router.message(Command("book"))
async def cmd_book(msg: Message, state: FSMContext) -> None:
    from .booking import begin_booking

    await begin_booking(msg, state)


@router.message(Command("my"))
async def cmd_my(msg: Message, state: FSMContext) -> None:
    from .my_bookings import _show_list

    await _show_list(msg, state)


@router.message(Command("faq"))
async def cmd_faq(msg: Message, state: FSMContext) -> None:
    await faq_list(msg, state)


@router.message(Command("ask"))
async def cmd_ask(msg: Message, state: FSMContext) -> None:
    await ask_start(msg, state)
