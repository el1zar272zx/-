# client_bot/keyboards.py
from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from shared.models import Booking, City, Service, ServiceCategory
from shared.pricing import price_plain
from shared.utils import chunked, fmt_date_short, fmt_price, fmt_time


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def back_button(callback_data: str = "back_main_menu") -> InlineKeyboardButton:
    return _btn("⬅️ Назад", callback_data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


BTN_BOOK = "📝 Записатися"
BTN_SERVICES = "📋 Послуги та ціни"
BTN_MY = "📆 Мої записи"
BTN_PROFILE = "👤 Мій профіль"
BTN_UPDATE_PHONE = "📱 Оновити номер"
BTN_INFO = "📍 Як нас знайти"
BTN_FAQ = "❓ Часті питання"
BTN_QUIZ = "🧭 Що мені підійде"
BTN_ASK = "💬 Запитати майстра"
BTN_CONTACT = "📱 Поділитися контактом"
BTN_USEFUL = "📚 Корисне"
BTN_CARE = "🌿 Мій догляд"


def T(btn: str) -> set[str]:
    """Текст кнопки меню українською та російською (клієнт з мовою ru бачить переклад)."""
    from shared.i18n import to_ru

    return {btn, to_ru(btn)}


def main_reply_kb() -> ReplyKeyboardMarkup:
    """Головне меню: кнопки завжди внизу екрана, не зникають."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_SERVICES), KeyboardButton(text=BTN_MY)],
            [
                KeyboardButton(text=BTN_PROFILE),
                KeyboardButton(text=BTN_UPDATE_PHONE, request_contact=True),
            ],
            [KeyboardButton(text=BTN_INFO), KeyboardButton(text=BTN_FAQ)],
            [KeyboardButton(text=BTN_QUIZ), KeyboardButton(text=BTN_ASK)],
            [KeyboardButton(text=BTN_USEFUL), KeyboardButton(text=BTN_CARE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Оберіть дію",
    )


def simple_reply_kb() -> ReplyKeyboardMarkup:
    """Простий режим: лише найнеобхідніше, великі зрозумілі кнопки."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_MY)],
            [KeyboardButton(text=BTN_INFO)],
            [KeyboardButton(text=BTN_ASK)],
            [KeyboardButton(text=BTN_PROFILE)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Оберіть дію",
    )


def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CONTACT, request_contact=True)]],
        resize_keyboard=True,
        is_persistent=True,
    )


# ---------- Запис ----------


def cities_kb(cities: list[City]) -> InlineKeyboardMarkup:
    rows = [[_btn(c.name, f"city_{c.id}")] for c in cities]
    rows.append([back_button("back_main_menu")])
    return _kb(rows)


def categories_kb(
    categories: list[ServiceCategory], back: str = "back_main_menu"
) -> InlineKeyboardMarkup:
    rows = [[_btn(c.name, f"cat_{c.id}")] for c in categories]
    rows.append([back_button(back)])
    return _kb(rows)


def services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(
                f"{s.name} ({s.duration_minutes} хв, {price_plain(s)})",
                f"service_{s.id}",
            )
        ]
        for s in services
    ]
    rows.append([back_button("back_category")])
    return _kb(rows)


def dates_kb(dates: list[date], preferred: set[int] | None = None) -> InlineKeyboardMarkup:
    """Лише робочі дні, по 3 в ряд. ⭐ біля зручних клієнту днів тижня."""
    preferred = preferred or set()
    rows = [
        [
            _btn(("⭐ " if d.weekday() in preferred else "") + fmt_date_short(d), f"date_{d.isoformat()}")
            for d in chunk
        ]
        for chunk in chunked(dates, 3)
    ]
    rows.append([back_button("back_service")])
    return _kb(rows)


PARTS = (("🌅 Ранок", 0, 12), ("☀️ День", 12, 17), ("🌆 Вечір", 17, 24))


def times_kb(times: list[str]) -> InlineKeyboardMarkup:
    """Час, згрупований за частинами доби (заголовки не натискаються)."""
    rows: list[list[InlineKeyboardButton]] = []
    for title, lo, hi in PARTS:
        part = [t for t in times if lo <= int(t[:2]) < hi]
        if part:
            rows.append([_btn(f"{title} · {len(part)}", "noop")])
            rows += [[_btn(t, f"time_{t}") for t in chunk] for chunk in chunked(part, 4)]
    rows.append([back_button("back_date")])
    return _kb(rows)


def payment_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("💵 Готівка", "pay_cash")],
            [_btn("💳 Картка", "pay_card")],
            [back_button("back_date")],
        ]
    )


def confirm_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("✅ Підтвердити запис", "confirm_booking")],
            [_btn("✍️ Побажання майстру", "wish_start")],
            [_btn("👥 Записати іншу людину", "guest_start")],
            [_btn("❌ Скасувати", "cancel_flow")],
        ]
    )


# ---------- Мої записи ----------


def my_bookings_kb(bookings: list[Booking]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn(
                f"{b.date.strftime('%d.%m')} {fmt_time(b.start_time)} · {b.title[:22]} · {fmt_price(b.price)}",
                f"myb_{b.id}",
            )
        ]
        for b in bookings
    ]
    rows.append([_btn("🔁 Повторити минулий запис", "repeat_last")])
    rows.append([back_button("back_main_menu")])
    return _kb(rows)


def reminder_kb(booking_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("✅ Буду, дякую", f"remok_{booking_id}")],
            [
                _btn("🔄 Перенести", f"mybmove_{booking_id}"),
                _btn("❌ Скасувати", f"mybcancel_{booking_id}"),
            ],
        ]
    )


def move_dates_kb(booking_id: int, dates: list[date]) -> InlineKeyboardMarkup:
    rows = [
        [_btn(fmt_date_short(d), f"mvd_{booking_id}_{d.isoformat()}") for d in chunk]
        for chunk in chunked(dates, 3)
    ]
    rows.append([back_button(f"myb_{booking_id}")])
    return _kb(rows)


def move_times_kb(booking_id: int, day: date, times: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [_btn(t, f"mvt_{booking_id}_{day.isoformat()}_{t.replace(':', '')}") for t in chunk]
        for chunk in chunked(times, 4)
    ]
    rows.append([back_button(f"mybmove_{booking_id}")])
    return _kb(rows)


def pay_url_button(url: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="💳 Оплатити онлайн", url=url)]


def booking_manage_kb(booking_id: int, pay_url: str = "") -> InlineKeyboardMarkup:
    return _kb(
        [
            *([pay_url_button(pay_url)] if pay_url.startswith("http") else []),
            [_btn("🔄 Перенести запис", f"mybmove_{booking_id}")],
            [_btn("🔁 На наступний тиждень, той самий час", f"mybnext_{booking_id}")],
            [_btn("📆 Додати в календар", f"ics_{booking_id}")],
            [_btn("📷 Фото зони (за бажанням)", f"zone_{booking_id}")],
            [_btn("❌ Скасувати запис", f"mybcancel_{booking_id}")],
            [back_button("back_my_bookings")],
        ]
    )


def booking_cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("Так, скасувати", f"mybcancelyes_{booking_id}")],
            [_btn("Ні, залишити", f"myb_{booking_id}")],
        ]
    )


# ---------- Перегляд послуг ----------


def services_categories_view_kb(
    categories: list[ServiceCategory],
) -> InlineKeyboardMarkup:
    rows = [[_btn(c.name, f"view_cat_{c.id}")] for c in categories]
    rows.append([back_button("back_main_menu")])
    return _kb(rows)


def services_view_list_kb(services: list[Service]) -> InlineKeyboardMarkup:
    rows = [
        [
            _btn(
                f"{s.name} — {s.duration_minutes} хв — {price_plain(s)}",
                f"view_service_{s.id}",
            )
        ]
        for s in services
    ]
    rows.append([back_button("menu_services")])
    return _kb(rows)


def service_details_kb(
    service_id: int, category_id: int, works: int = 0, fav: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    if works:
        rows.append([_btn(f"📸 Приклади робіт ({works})", f"view_works_{service_id}")])
    rows.append([_btn("📝 Записатися на цю послугу", f"view_book_{service_id}")])
    rows.append(
        [
            _btn("💔 Прибрати з обраного" if fav else "⭐ В обране", f"fav_{service_id}"),
            _btn("⚖️ Порівняти", f"cmp_{service_id}"),
        ]
    )
    rows.append([back_button(f"view_cat_{category_id}")])
    return _kb(rows)


def works_after_kb(service_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("📝 Записатися на цю послугу", f"view_book_{service_id}")],
            [_btn("⬅️ До послуги", f"view_service_{service_id}")],
        ]
    )


# ---------- Кошик послуг, очікування, відгуки, профіль ----------


def cart_kb(
    can_add: bool = True, together: list | None = None, pair: bool = False, can_pair: bool = True
) -> InlineKeyboardMarkup:
    rows = []
    if can_add:
        for s in together or []:
            rows.append([_btn(f"➕ {s.name[:30]} · {s.duration_minutes} хв", f"service_{s.id}")])
        rows.append([_btn("➕ Додати ще послугу", "cart_add")])
    if pair or can_pair:
        rows.append([_btn("✅ Вдвох: час подвоєно" if pair else "👭 Прийти вдвох", "cart_pair")])
    rows.append([_btn("📅 Обрати дату", "cart_dates")])
    rows.append([_btn("🗑 Змінити вибір", "cart_clear")])
    return _kb(rows)


def waitlist_kb(iso_date: str) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("🔔 Повідомити, якщо звільниться", f"wl_{iso_date}")],
            [back_button("back_date")],
        ]
    )


def rating_kb(booking_id: int) -> InlineKeyboardMarkup:
    return _kb([[_btn(f"{n}⭐", f"rate_{booking_id}_{n}") for n in range(1, 6)]])


def profile_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("✏️ Змінити імʼя", "profile_name")],
            [_btn("🎂 Дата народження", "profile_bday")],
            [_btn("📝 Про мене", "profile_about")],
            [_btn("🗓 Зручні дні тижня", "profile_days")],
            [_btn("📞 Як зв'язуватися", "profile_contact")],
            [_btn("🌙 Тихі години", "profile_quiet")],
            [_btn("⭐ Обрані послуги", "favs"), _btn("📈 Мій прогрес", "prog")],
            [_btn("💡 Поради тижня", "profile_tips"), _btn("🔎 Простий режим", "profile_simple")],
            [_btn("🌐 Мова / Язык", "profile_lang")],
        ]
    )


def quiet_kb(current: str | None) -> InlineKeyboardMarkup:
    from shared.quiet import OPTIONS

    rows = [
        [_btn(("✅ " if v == current else "") + text, f"qh_{key}")]
        for key, (text, v) in OPTIONS.items()
    ]
    rows.append([_btn("Готово ➡️", "profile_back")])
    return _kb(rows)


DAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")


def pref_days_kb(selected: set[int]) -> InlineKeyboardMarkup:
    row1 = [_btn(("✅ " if i in selected else "") + DAY_NAMES[i], f"pd_{i}") for i in range(4)]
    row2 = [_btn(("✅ " if i in selected else "") + DAY_NAMES[i], f"pd_{i}") for i in range(4, 7)]
    return _kb([row1, row2, [_btn("Готово ➡️", "profile_back")]])


INTAKE_ITEMS = (
    "Вагітність",
    "Підвищений тиск",
    "Варикоз / тромбоз",
    "Шкірні захворювання, рани",
    "Алергії",
    "Онкологія (зараз або раніше)",
)


def intake_kb(selected: list[int]) -> InlineKeyboardMarkup:
    rows = [
        [_btn(("✅ " if i in selected else "⬜ ") + name, f"intk_{i}")]
        for i, name in enumerate(INTAKE_ITEMS)
    ]
    rows.append([_btn("Готово ➡️", "intk_done")])
    return _kb(rows)


def recall_kb(service_id: int) -> InlineKeyboardMarkup:
    return _kb([[_btn("📅 Обрати час", f"recall_{service_id}")]])


def followup_kb(booking_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("👍 Усе добре", f"fu_ok_{booking_id}")],
            [_btn("😕 Мене щось турбує", f"fu_bad_{booking_id}")],
        ]
    )


def repeat_kb() -> InlineKeyboardMarkup:
    return _kb([[_btn("🔁 Повторити минулий запис", "repeat_last")]])


def back_only_kb(callback_data: str) -> InlineKeyboardMarkup:
    return _kb([[back_button(callback_data)]])
