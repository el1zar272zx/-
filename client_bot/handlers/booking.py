# client_bot/handlers/booking.py
from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from admin_bot.notify import notify_new_booking
from shared.database import session_scope
from shared.models import PaymentMethod, Service
from shared.notifications_soft import (
    PAYMENT_UA,
    format_booking_created_for_client,
)
from shared.services import (
    BookingError,
    add_waitlist,
    create_booking_atomic,
    get_active_cities,
    get_available_time_slots,
    get_card_settings,
    get_categories,
    get_city,
    get_client,
    get_service,
    get_services_by_category,
    get_working_dates,
)
from shared.tg import Event, ack, respond, safe_edit
from shared.utils import (
    esc,
    fmt_date_long,
    fmt_price,
    fmt_time,
    parse_cb_int,
)

from ..keyboards import (
    T,
    BTN_BOOK,
    back_only_kb,
    cart_kb,
    categories_kb,
    cities_kb,
    confirm_kb,
    dates_kb,
    main_reply_kb,
    pay_url_button,
    payment_kb,
    services_kb,
    times_kb,
    waitlist_kb,
)
from shared.pricing import price_html

from ..states import BookingStates

logger = logging.getLogger(__name__)
router = Router(name="client_booking")

MAX_SERVICES = 4
MAX_PENDING = 3  # не більше стількох заявок, що чекають підтвердження


# ---------- Хелпери ----------


def _total(services: list[Service]) -> tuple[int, int | None]:
    """(сумарна тривалість, сумарна ціна або None, якщо в якоїсь послуги ціни немає)."""
    minutes = sum(s.duration_minutes for s in services)
    from shared.pricing import eff_price

    prices = [eff_price(s) for s in services]
    price = None if any(p is None for p in prices) else sum(prices)
    return minutes, price


def _ticket(services: list[Service], chosen: date | None = None, start: str | None = None) -> str:
    """Постійна «шапка візиту»: що обрано і скільки це коштує. Видно на кожному кроці запису."""
    minutes, price = _total(services)
    lines = [
        "🧾 <b>Ваш візит</b>",
        f"💆 {esc(' + '.join(s.name for s in services))}",
        f"⏱ {minutes} хв · 💰 <b>{fmt_price(price)}</b>",
    ]
    if chosen:
        lines.append(f"📅 {fmt_date_long(chosen)}" + (f" · 🕐 {start}" if start else ""))
    return "\n".join(lines) + "\n\n"


async def _load_services(ids: list[int]) -> list[Service]:
    async with session_scope() as session:
        loaded = [await get_service(session, i) for i in ids]
    return [s for s in loaded if s is not None and s.is_active]


async def _restart(call: CallbackQuery, state: FSMContext) -> None:
    """Дані сценарію загублено (наприклад, бот перезапускали) — починаємо спочатку."""
    await state.clear()
    await call.answer("Сеанс запису застарів. Почніть, будь ласка, заново.", show_alert=True)
    await safe_edit(call, "Оберіть дію кнопками внизу 👇")


async def _show_cities(event: Event, state: FSMContext) -> None:
    async with session_scope() as session:
        cities = await get_active_cities(session)
    if not cities:
        await ack(event, "Запис тимчасово недоступний.", alert=True)
        return
    await state.set_state(BookingStates.CHOOSING_CITY)
    await ack(event)
    await respond(event, "Оберіть місто:", cities_kb(cities))


async def _show_categories(event: Event, state: FSMContext) -> None:
    async with session_scope() as session:
        categories = await get_categories(session)
        cities = await get_active_cities(session)
    if not categories:
        await ack(event, "Послуги тимчасово недоступні.", alert=True)
        return
    # Одне місто — крок вибору міста пропущено, «Назад» веде в меню
    back = "back_city" if len(cities) > 1 else "back_main_menu"
    await state.set_state(BookingStates.CHOOSING_CATEGORY)
    await ack(event)
    await respond(event, "Оберіть категорію:", categories_kb(categories, back))


async def _show_services(event: Event, state: FSMContext) -> None:
    data = await state.get_data()
    cat_id = data.get("category_id")
    if cat_id is None:
        await _show_categories(event, state)
        return
    async with session_scope() as session:
        services = await get_services_by_category(session, cat_id)
    if not services:
        await ack(event, "У цій категорії поки немає активних послуг.", alert=True)
        return
    await state.set_state(BookingStates.CHOOSING_SERVICE)
    await ack(event)
    await respond(event, "Оберіть послугу:", services_kb(services))


async def _show_cart(event: Event, state: FSMContext) -> None:
    """Підсумок обраних послуг: можна додати ще одну або перейти до дати."""
    await state.update_data(safety_ok=False, safety_has=False)  # вибір змінився: питання безпеки задамо знову
    data = await state.get_data()
    services = await _load_services(list(data.get("service_ids", [])))
    if not services:
        await state.update_data(service_ids=[])
        await _show_categories(event, state)
        return
    minutes, price = _total(services)
    lines = ["<b>Ваш вибір:</b>"]
    for i, s in enumerate(services, 1):
        lines.append(f"{i}. {esc(s.name)} — {s.duration_minutes} хв, {price_html(s)}")
    pair = bool(data.get("pair"))
    if len(services) > 1:
        lines.append(f"\nРазом: {minutes} хв, {fmt_price(price)}")
    if pair:
        lines.append("\n👭 Запис вдвох: час подвоєно, ви підете одна за одною.")
    lines.append("\nМожна додати ще послугу в цей же візит або перейти до вибору дати.")
    await state.set_state(BookingStates.CHOOSING_SERVICE)
    await ack(event)
    together = []
    if len(services) < MAX_SERVICES and not pair:
        try:
            from shared.together import suggest

            together = await suggest([s.id for s in services])
        except Exception:
            logger.exception("Не вдалося підібрати «часто беруть разом»")
    if together:
        lines.append("\n💡 <b>Часто беруть разом:</b> " + ", ".join(esc(s.name) for s in together))
    try:
        from shared import drafts

        await drafts.save(event.from_user.id, list(data.get("service_ids", [])), data.get("city_id"))
    except Exception:
        logger.exception("Чернетку запису не збережено")
    await respond(
        event,
        "\n".join(lines),
        cart_kb(can_add=len(services) < MAX_SERVICES and not pair, together=together, pair=pair),
    )


async def _show_dates(
    call: CallbackQuery, state: FSMContext, *, answered: bool = False
) -> None:
    """answered=True, якщо на цей callback вже відповіли (Telegram дозволяє лише одну відповідь)."""
    data = await state.get_data()
    city_id = data.get("city_id")
    if city_id is None or not data.get("service_ids"):
        await _restart(call, state)
        return
    async with session_scope() as session:
        dates = await get_working_dates(session, city_id)
        client = await get_client(session, call.from_user.id)
    preferred = {int(x) for x in (client.pref_days or "").split(",") if x.strip().isdigit()} if client else set()
    if not dates:
        if answered:
            await state.clear()
            await safe_edit(call, "Наразі немає днів для запису. Спробуйте пізніше.")
        else:
            await call.answer(
                "Наразі немає днів для запису. Спробуйте пізніше.", show_alert=True
            )
        return
    await state.set_state(BookingStates.CHOOSING_DATE)
    if not answered:
        await call.answer()
    services = await _load_services(list(data.get("service_ids", [])))
    head = _ticket(services) if services else ""
    note = "\n⭐ позначені дні, які вам зручні." if preferred else ""
    await safe_edit(call, head + "Оберіть дату:" + note, dates_kb(dates, preferred))


async def begin_booking(event: Event, state: FSMContext) -> None:
    """Початок запису. Якщо місто одне — одразу до категорій."""
    await state.clear()
    async with session_scope() as session:
        cities = await get_active_cities(session)
    if not cities:
        await ack(event, "Запис тимчасово недоступний.", alert=True)
        return
    if len(cities) == 1:
        await state.update_data(city_id=cities[0].id, service_ids=[])
        async with session_scope() as session:
            client = await get_client(session, event.from_user.id)
        if client is not None and client.intake is None:
            from .extras import ask_intake

            await ask_intake(event, state)
            return
        await _show_categories(event, state)
    else:
        await _show_cities(event, state)


# ---------- Основний сценарій ----------


@router.message(F.text.in_(T(BTN_BOOK)))
async def start_booking(msg: Message, state: FSMContext) -> None:
    await begin_booking(msg, state)


@router.callback_query(BookingStates.CHOOSING_CITY, F.data.startswith("city_"))
async def choose_city(call: CallbackQuery, state: FSMContext) -> None:
    city_id = parse_cb_int(call.data, "city_")
    if city_id is None:
        await call.answer()
        return
    await state.update_data(city_id=city_id)
    data = await state.get_data()
    if data.get("service_ids"):  # послугу вже обрано в каталозі
        await _show_cart(call, state)
    else:
        await _show_categories(call, state)


@router.callback_query(BookingStates.CHOOSING_CATEGORY, F.data.startswith("cat_"))
async def choose_category(call: CallbackQuery, state: FSMContext) -> None:
    cat_id = parse_cb_int(call.data, "cat_")
    if cat_id is None:
        await call.answer()
        return
    await state.update_data(category_id=cat_id)
    await _show_services(call, state)


@router.callback_query(BookingStates.CHOOSING_SERVICE, F.data.startswith("service_"))
async def choose_service(call: CallbackQuery, state: FSMContext) -> None:
    service_id = parse_cb_int(call.data, "service_")
    if service_id is None:
        await call.answer()
        return
    data = await state.get_data()
    ids = list(data.get("service_ids", []))
    if service_id not in ids:
        if len(ids) >= MAX_SERVICES:
            await call.answer(f"Не більше {MAX_SERVICES} послуг за візит.", show_alert=True)
            return
        ids.append(service_id)
    await state.update_data(service_ids=ids)
    await _show_cart(call, state)


@router.callback_query(F.data == "cart_add")
async def cart_add(call: CallbackQuery, state: FSMContext) -> None:
    await _show_categories(call, state)


@router.callback_query(F.data == "cart_clear")
async def cart_clear(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(pair=False)
    await state.update_data(service_ids=[])
    await _show_categories(call, state)


@router.callback_query(F.data == "cart_dates")
async def cart_dates(call: CallbackQuery, state: FSMContext) -> None:
    await _show_dates(call, state)


@router.callback_query(BookingStates.CHOOSING_DATE, F.data.startswith("date_"))
async def choose_date(call: CallbackQuery, state: FSMContext) -> None:
    try:
        chosen = date.fromisoformat(call.data.split("_", 1)[1])
    except ValueError:
        await call.answer()
        return

    data = await state.get_data()
    city_id, service_ids = data.get("city_id"), data.get("service_ids")
    if city_id is None or not service_ids:
        await _restart(call, state)
        return

    services = await _load_services(list(service_ids))
    if not services:
        await _restart(call, state)
        return
    duration, _ = _total(services)
    async with session_scope() as session:
        slots = await get_available_time_slots(
            session, city_id=city_id, for_date=chosen, duration_minutes=duration
        )
    if not slots:
        await call.answer()
        await safe_edit(
            call,
            f"На {fmt_date_long(chosen)} вільних годин немає.\n"
            "Можемо повідомити вас, якщо місце звільниться.",
            waitlist_kb(chosen.isoformat()),
        )
        return

    await state.update_data(date=chosen.isoformat())
    await state.set_state(BookingStates.CHOOSING_TIME)
    await call.answer()
    await safe_edit(
        call,
        _ticket(services, chosen) + f"Оберіть час на {fmt_date_long(chosen)}:",
        times_kb([fmt_time(t) for t in slots]),
    )


@router.callback_query(F.data.startswith("wl_"))
async def join_waitlist(call: CallbackQuery, state: FSMContext) -> None:
    try:
        chosen = date.fromisoformat(call.data.split("_", 1)[1])
    except ValueError:
        await call.answer()
        return
    data = await state.get_data()
    city_id, service_ids = data.get("city_id"), data.get("service_ids")
    services = await _load_services(list(service_ids or []))
    if city_id is None or not services:
        await _restart(call, state)
        return
    duration, _ = _total(services)
    async with session_scope() as session:
        created = await add_waitlist(session, call.from_user.id, city_id, chosen, duration)
    await call.answer("Готово 🔔" if created else "Ви вже в списку очікування.")
    await safe_edit(
        call,
        f"🔔 Добре! Якщо на {fmt_date_long(chosen)} звільниться місце, ми напишемо вам.",
        back_only_kb("back_date"),
    )


@router.callback_query(BookingStates.CHOOSING_TIME, F.data.startswith("time_"))
async def choose_time(call: CallbackQuery, state: FSMContext) -> None:
    t_str = call.data.split("_", 1)[1]
    try:
        datetime.strptime(t_str, "%H:%M")
    except ValueError:
        await call.answer()
        return
    await state.update_data(time=t_str)
    await state.set_state(BookingStates.CHOOSING_PAYMENT)
    await call.answer()
    data = await state.get_data()
    try:
        pay_services = await _load_services(list(data.get("service_ids", [])))
        pay_head = _ticket(pay_services, date.fromisoformat(data["date"]), t_str) if pay_services else ""
    except (KeyError, ValueError):
        pay_head = ""
    await safe_edit(call, pay_head + "Оберіть спосіб оплати:", payment_kb())


@router.callback_query(
    BookingStates.CHOOSING_PAYMENT, F.data.in_({"pay_cash", "pay_card"})
)
async def choose_payment(call: CallbackQuery, state: FSMContext) -> None:
    payment = PaymentMethod.CARD if call.data == "pay_card" else PaymentMethod.CASH
    await state.update_data(payment_method=payment.value)
    data = await state.get_data()
    try:
        city_id = data["city_id"]
        service_ids = list(data["service_ids"])
        book_date = date.fromisoformat(data["date"])
        start = datetime.strptime(data["time"], "%H:%M").time()
    except (KeyError, ValueError):
        await _restart(call, state)
        return

    text = await _confirmation_text(call.from_user.id, state)
    if text is None:
        await _restart(call, state)
        return
    await state.set_state(BookingStates.CONFIRMATION)
    await call.answer()
    await safe_edit(call, text, confirm_kb())


async def _confirmation_text(user_id: int, state: FSMContext) -> str | None:
    """Підсумок перед підтвердженням. None, якщо дані сценарію загублено."""
    data = await state.get_data()
    try:
        city_id = data["city_id"]
        service_ids = list(data["service_ids"])
        book_date = date.fromisoformat(data["date"])
        start = datetime.strptime(data["time"], "%H:%M").time()
        payment = PaymentMethod(data["payment_method"])
    except (KeyError, ValueError):
        return None
    services = await _load_services(service_ids)
    async with session_scope() as session:
        city = await get_city(session, city_id)
        client = await get_client(session, user_id)
    if not services or len(services) != len(service_ids) or not city or not client:
        return None
    minutes, price = _total(services)
    guest_name, guest_phone = data.get("guest_name"), data.get("guest_phone")
    who = (
        f"👤 Для кого: {esc(guest_name)}\n📞 Телефон: {esc(guest_phone)}\n(записала {esc(client.name)})"
        if guest_name and guest_phone
        else f"👤 Імʼя: {esc(client.name)}\n📞 Телефон: {esc(client.phone)}"
    )
    shown = services[: len(services) // 2] if data.get("pair") else services
    names = esc(" + ".join(s.name for s in shown)) + (" ×2 (вдвох)" if data.get("pair") else "")
    level_line = ""
    try:
        from shared import levels
        from shared.services import count_client_visits

        async with session_scope() as session:
            visits = await count_client_visits(session, user_id)
        pct = levels.pct_for(visits)
        if pct and price:
            level_line = f"\n🏅 Знижка вашого рівня: {pct}%"
    except Exception:
        logger.exception("Знижка рівня не обчислена")
    return (
        "Перевірте, будь ласка, дані перед підтвердженням:\n\n"
        f"💆 Послуга: {names}\n"
        f"📍 Місто: {esc(city.name)}\n"
        f"📅 Дата: {fmt_date_long(book_date)}\n"
        f"🕐 Час: {fmt_time(start)}\n"
        f"⏱ Тривалість: {minutes} хв\n"
        f"💰 Вартість: <b>{fmt_price(price)}</b>{level_line}\n"
        f"💳 Оплата: {PAYMENT_UA[payment]}\n\n"
        + who
        + (f"\n✍️ Побажання: {esc(data['wish'])}" if data.get("wish") else "")
    )


async def _flag_abuse(msg, admin_bot) -> None:
    """Після 3 грубих спроб тихо повідомляємо майстра."""
    from shared import namefilter

    if namefilter.strike(msg.from_user.id) != 3 or admin_bot is None:
        return
    try:
        from shared.services import get_active_admin_ids

        async with session_scope() as session:
            ids = await get_active_admin_ids(session)
        uname = f"@{msg.from_user.username}" if msg.from_user.username else "без username"
        for admin_id in ids:
            await admin_bot.send_message(
                admin_id,
                f"🚩 Користувач {esc(msg.from_user.full_name)} ({uname}, id {msg.from_user.id}) "
                "тричі ввів грубі слова в імені чи тексті. Нічого не заблоковано; "
                "за потреби вимкніть самостійний запис у картці клієнта.",
            )
    except Exception:
        logger.exception("Не вдалося повідомити про грубі слова")


# ---------- Побажання майстру ----------


@router.callback_query(BookingStates.CONFIRMATION, F.data == "wish_start")
async def wish_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingStates.WISH)
    await call.answer()
    await safe_edit(
        call,
        "✍️ Напишіть побажання майстру одним повідомленням. Наприклад: «болить права лопатка», "
        "«не люблю сильний тиск», «перший раз на ботокс, хвилююсь».\n"
        "Щоб прибрати побажання, надішліть «-».",
        back_only_kb("wish_cancel"),
    )


@router.callback_query(F.data == "wish_cancel")
async def wish_cancel(call: CallbackQuery, state: FSMContext) -> None:
    text = await _confirmation_text(call.from_user.id, state)
    if text is None:
        await _restart(call, state)
        return
    await state.set_state(BookingStates.CONFIRMATION)
    await call.answer()
    await safe_edit(call, text, confirm_kb())


@router.message(BookingStates.WISH, F.text)
async def wish_save(msg: Message, state: FSMContext, admin_bot: Bot | None = None) -> None:
    raw = " ".join(msg.text.split())
    from shared import namefilter

    if namefilter.has_obscene(raw):
        await _flag_abuse(msg, admin_bot)
        await msg.answer(namefilter.TEXT_REJECT)
        return
    await state.update_data(wish=None if raw in ("-", "—", "") else raw[:300])
    text = await _confirmation_text(msg.from_user.id, state)
    if text is None:
        await state.clear()
        await msg.answer("Сеанс запису застарів. Почніть, будь ласка, заново.")
        return
    await state.set_state(BookingStates.CONFIRMATION)
    await msg.answer(text, reply_markup=confirm_kb())


# ---------- Запис іншої людини ----------


@router.callback_query(BookingStates.CONFIRMATION, F.data == "guest_start")
async def guest_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingStates.GUEST_NAME)
    await call.answer()
    await safe_edit(
        call,
        "👥 Напишіть імʼя людини, яку ви записуєте (наприклад, «Олена»).\n"
        "Нагадування й повідомлення про запис прийдуть вам.",
        back_only_kb("guest_cancel"),
    )


@router.callback_query(F.data == "guest_cancel")
async def guest_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(guest_name=None, guest_phone=None)
    text = await _confirmation_text(call.from_user.id, state)
    if text is None:
        await _restart(call, state)
        return
    await state.set_state(BookingStates.CONFIRMATION)
    await call.answer()
    await safe_edit(call, text, confirm_kb())


@router.message(BookingStates.GUEST_NAME, F.text)
async def guest_name(msg: Message, state: FSMContext, admin_bot: Bot | None = None) -> None:
    name = " ".join(msg.text.split())[:60]
    if len(name) < 2:
        await msg.answer("Напишіть імʼя, будь ласка (мінімум 2 літери).")
        return
    from shared import namefilter

    reason = namefilter.check_name(name)
    if reason:
        await _flag_abuse(msg, admin_bot)
        await msg.answer(namefilter.MESSAGES[reason])
        return
    await state.update_data(guest_name=name)
    await state.set_state(BookingStates.GUEST_PHONE)
    await msg.answer("📞 Тепер номер телефону цієї людини, наприклад +380671234567:")


@router.message(BookingStates.GUEST_PHONE, F.text)
async def guest_phone(msg: Message, state: FSMContext) -> None:
    from shared.utils import normalize_phone

    phone = normalize_phone(msg.text)
    if phone is None:
        await msg.answer("Не вдалося розпізнати номер. Приклад: +380671234567")
        return
    await state.update_data(guest_phone="+" + phone.lstrip("+"))
    text = await _confirmation_text(msg.from_user.id, state)
    if text is None:
        await state.clear()
        await msg.answer("Сеанс запису застарів. Почніть, будь ласка, заново.")
        return
    await state.set_state(BookingStates.CONFIRMATION)
    await msg.answer(text, reply_markup=confirm_kb())


def _safety_text(services: list[Service]) -> str:
    from shared.contra import CONSENT_TEXT, items

    lines = ["🌿 <b>Перед записом: безпека</b>", ""]
    needs_consent = False
    for s in services:
        lines.append(f"<b>{esc(s.name)}</b>")
        its = items(s)
        if its:
            lines += [f"• {esc(x)}" for x in its]
        else:
            lines.append("• Протипоказання уточнить майстер")
        lines.append("")
        needs_consent = needs_consent or bool(getattr(s, "consent", False))
    lines.append("Це загальні орієнтири, майстер уточнить деталі перед сеансом.")
    if needs_consent:
        lines += ["", f"<i>{esc(CONSENT_TEXT)}</i>"]
    lines += ["", "Чи є у вас щось із перелічених пунктів?"]
    return "\n".join(lines)


def _safety_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Немає, погоджуюсь", callback_data="safe_ok")],
            [InlineKeyboardButton(text="⚠️ Є, зв'яжіться зі мною", callback_data="safe_has")],
            [InlineKeyboardButton(text="❌ Скасувати", callback_data="cancel_flow")],
        ]
    )


@router.callback_query(BookingStates.CONFIRMATION, F.data.in_({"safe_ok", "safe_has"}))
async def safety_answer(
    call: CallbackQuery, state: FSMContext, admin_bot: Bot | None = None
) -> None:
    await state.update_data(safety_ok=True, safety_has=(call.data == "safe_has"))
    await confirm_booking(call, state, admin_bot)


@router.callback_query(BookingStates.CONFIRMATION, F.data == "confirm_booking")
async def confirm_booking(
    call: CallbackQuery, state: FSMContext, admin_bot: Bot | None = None
) -> None:
    data = await state.get_data()
    try:
        city_id = int(data["city_id"])
        service_ids = [int(x) for x in data["service_ids"]]
        book_date = date.fromisoformat(data["date"])
        start = datetime.strptime(data["time"], "%H:%M").time()
        payment = PaymentMethod(data["payment_method"])
    except (KeyError, ValueError):
        await _restart(call, state)
        return
    async with session_scope() as session:
        client = await get_client(session, call.from_user.id)
    if client is None:
        await _restart(call, state)
        return

    guest_name, guest_phone = data.get("guest_name"), data.get("guest_phone")

    # Крок безпеки: протипоказання й згода (лише для послуг, де це налаштовано)
    if not data.get("safety_ok"):
        try:
            from shared.contra import needs_check

            checked = [s for s in await _load_services(service_ids) if needs_check(s)]
        except Exception:
            logger.exception("Не вдалося перевірити протипоказання")
            checked = []
        if checked:
            await state.update_data(safety_names=[s.name for s in checked])
            await call.answer()
            await safe_edit(call, _safety_text(checked), _safety_kb())
            return

    from sqlalchemy import func, select

    from shared.models import Booking, BookingStatus

    async with session_scope() as session:
        waiting = await session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.client_telegram_id == call.from_user.id,
                Booking.status == BookingStatus.PENDING,
                Booking.date >= date.today(),
            )
        )
    if (waiting or 0) >= MAX_PENDING:
        await call.answer(
            f"У вас вже {waiting} заявок, що чекають підтвердження. "
            "Дочекайтесь відповіді майстра або скасуйте зайві.",
            show_alert=True,
        )
        return

    # Знімаємо стан одразу: повторне натискання кнопки не створить дубль запису
    await state.set_state(None)

    try:
        booking = await create_booking_atomic(
            city_id=city_id,
            service_ids=service_ids,
            client_telegram_id=call.from_user.id,
            client_name=(
                f"{guest_name} (записала {client.name})"[:150] if guest_name and guest_phone else client.name
            ),
            client_phone=guest_phone if guest_name and guest_phone else client.phone,
            booking_date=book_date,
            start_time=start,
            payment_method=payment,
        )
    except BookingError as e:
        await call.answer(str(e), show_alert=True)
        await _show_dates(call, state, answered=True)
        return
    except Exception:
        logger.exception("Не вдалося створити запис")
        await state.clear()
        await call.answer("Сталася помилка. Спробуйте ще раз пізніше.", show_alert=True)
        await safe_edit(call, "Оберіть дію кнопками внизу 👇")
        return

    wish = data.get("wish")
    if wish:
        try:
            async with session_scope() as session:
                from shared.models import Booking as _B

                row = await session.get(_B, booking.id)
                if row is not None:
                    row.wish = wish
            booking.wish = wish
        except Exception:
            logger.exception("Не вдалося зберегти побажання до запису #%s", booking.id)

    try:
        await _finish_booking(booking, client, data, service_ids)
    except Exception:
        logger.exception("Не вдалося доповнити запис #%s (прапорці, згода)", booking.id)

    async with session_scope() as session:
        card = await get_card_settings(session)

    await state.clear()
    await call.answer("Заявку створено ✅")
    from shared import salon

    pay_url = salon.info("pay_url")
    pay_markup = (
        InlineKeyboardMarkup(inline_keyboard=[pay_url_button(pay_url)])
        if booking.payment_method == PaymentMethod.CARD and pay_url.startswith("http")
        else None
    )
    await safe_edit(call, format_booking_created_for_client(booking, card), pay_markup)
    await call.message.answer(
        "Статус заявки дивіться в «📆 Мої записи».", reply_markup=main_reply_kb()
    )

    try:
        await notify_new_booking(admin_bot, booking)
    except Exception:
        logger.exception("Не вдалося повідомити адміністраторів про запис #%s", booking.id)


async def _finish_booking(booking, client, data: dict, service_ids: list[int]) -> None:
    """Червоні прапорці, запис згоди, назва для запису вдвох, прибрати чернетку."""
    from shared import drafts
    from shared.contra import CONSENT_TEXT
    from shared.models import Booking as _B, ConsentRecord
    from shared.redflags import collect

    extra = ["клієнт позначив протипоказання, уточніть"] if data.get("safety_has") else []
    flags = collect(client, extra)
    services = await _load_services(service_ids)
    async with session_scope() as session:
        row = await session.get(_B, booking.id)
        if row is not None:
            if flags:
                row.flags = "; ".join(flags)[:300]
            if data.get("pair") and services:
                half = services[: len(services) // 2]
                row.service_title = (" + ".join(s.name for s in half) + " ×2 (вдвох)")[:400]
            if data.get("pair"):
                note = "Прийдуть вдвох"
                row.wish = f"{note}. {row.wish}" if row.wish else note
        if data.get("safety_ok") and not data.get("safety_has"):
            for s in services:
                if getattr(s, "consent", False):
                    session.add(
                        ConsentRecord(
                            client_telegram_id=client.telegram_id,
                            service_id=s.id,
                            booking_id=booking.id,
                            text=CONSENT_TEXT,
                        )
                    )
    booking.flags = "; ".join(flags)[:300] if flags else None
    if data.get("pair") and services:
        booking.service_title = (" + ".join(s.name for s in services[: len(services) // 2]) + " ×2 (вдвох)")[:400]
        booking.wish = ("Прийдуть вдвох. " + booking.wish) if booking.wish else "Прийдуть вдвох"
    await drafts.clear(client.telegram_id)


@router.callback_query(F.data == "cancel_flow")
async def cancel_flow(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, "Запис скасовано.")


# ---------- Кнопки «Назад» всередині сценарію ----------


@router.callback_query(F.data == "back_city")
async def back_city(call: CallbackQuery, state: FSMContext) -> None:
    await _show_cities(call, state)


@router.callback_query(F.data == "back_category")
async def back_category(call: CallbackQuery, state: FSMContext) -> None:
    await _show_categories(call, state)


@router.callback_query(F.data == "back_service")
async def back_service(call: CallbackQuery, state: FSMContext) -> None:
    await _show_cart(call, state)


@router.callback_query(F.data == "back_date")
async def back_date(call: CallbackQuery, state: FSMContext) -> None:
    await _show_dates(call, state)
