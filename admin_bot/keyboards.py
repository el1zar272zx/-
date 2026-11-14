# admin_bot/keyboards.py
from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from shared.models import (
    AdminUser,
    BlockedSlot,
    Booking,
    BookingStatus,
    City,
    Schedule,
    ScheduleException,
    Service,
    ServiceCategory,
)
from shared.utils import (
    WEEKDAYS_SHORT,
    chunked,
    fmt_date_short,
    fmt_hours,
    fmt_price,
    fmt_time,
    now_local,
    today_local,
)


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back(data: str = "adm_menu") -> InlineKeyboardButton:
    return btn("⬅️ Назад", data)


def menu_only_kb() -> InlineKeyboardMarkup:
    return kb([[btn("🏠 Меню", "adm_menu")]])


# Кнопки нижнього меню (постійна клавіатура)
A_NEW = "📥 Нові заявки"
A_DAYS = "📅 Записи по днях"
A_SCHEDULE = "🗓 Графік роботи"
A_SERVICES = "💰 Послуги та ціни"
A_CARD = "💳 Реквізити картки"
A_REVIEWS = "⭐ Відгуки"
A_CALENDAR = "📆 У календар"
A_BROADCAST = "📣 Розсилка"
A_ADMINS = "👥 Адміністратори"
A_BACKUP = "💾 Бекап бази"
A_MANUAL = "➕ Записати клієнта"
A_CLIENTS = "🔎 Клієнти"
A_REPORT = "📊 Звіт"
A_DAYOFF = "🏖 Вихідні"
A_SALON = "🏠 Про салон"
A_WEEK = "🖼 Розклад тижня"
A_TOOLS = "🧰 Інструменти"


BAR_LABELS = {
    A_NEW, A_DAYS, A_SCHEDULE, A_SERVICES, A_CARD, A_REVIEWS, A_CALENDAR,
    A_BROADCAST, A_ADMINS, A_BACKUP, A_MANUAL, A_CLIENTS, A_REPORT, A_DAYOFF,
    A_SALON, A_WEEK, A_TOOLS,
}


def admin_reply_kb(is_super: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=A_NEW), KeyboardButton(text=A_MANUAL)],
        [KeyboardButton(text=A_DAYS), KeyboardButton(text=A_CLIENTS)],
        [KeyboardButton(text=A_SCHEDULE), KeyboardButton(text=A_SERVICES)],
        [KeyboardButton(text=A_REPORT), KeyboardButton(text=A_DAYOFF)],
        [KeyboardButton(text=A_WEEK), KeyboardButton(text=A_SALON)],
        [KeyboardButton(text=A_CARD), KeyboardButton(text=A_REVIEWS)],
        [KeyboardButton(text=A_CALENDAR), KeyboardButton(text=A_BROADCAST)],
        [KeyboardButton(text=A_TOOLS)],
    ]
    if is_super:
        rows.append([KeyboardButton(text=A_ADMINS), KeyboardButton(text=A_BACKUP)])
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True, is_persistent=True
    )


def _cnt(n: int | None) -> str:
    return f" ({n})" if n else ""


def main_menu_kb(is_super: bool, counts: dict | None = None) -> InlineKeyboardMarkup:
    counts = counts or {}
    rows = [
        [btn(A_NEW + _cnt(counts.get("new")), "abk_new"), btn(A_MANUAL, "man_start")],
        [btn(A_DAYS + _cnt(counts.get("today")), "abk_days"), btn(A_CLIENTS, "acl_start")],
        [btn(A_SCHEDULE, "asc_menu"), btn(A_SERVICES, "asv_menu")],
        [btn(A_REPORT, "rep_menu"), btn(A_DAYOFF, "off_start")],
        [btn(A_WEEK, "wk_img"), btn(A_SALON, "sal_menu")],
        [btn(A_CARD, "ast_card"), btn(A_REVIEWS + _cnt(counts.get("reviews")), "arv_list")],
        [btn(A_CALENDAR, "abk_cal"), btn(A_BROADCAST, "abc_start")],
        [btn(A_TOOLS + _cnt(counts.get("tasks")), "pro_menu")],
    ]
    if is_super:
        rows.append([btn(A_ADMINS, "ast_admins"), btn(A_BACKUP, "abc_backup")])
    return kb(rows)


def calendar_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("📆 Сьогодні", "abk_icsr_1")],
            [btn("🗓 Найближчі 7 днів", "abk_icsr_7")],
            [btn("📅 Найближчі 30 днів", "abk_icsr_30")],
            [btn("🏠 Меню", "adm_menu")],
        ]
    )


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("✅ Надіслати всім", "abc_yes")],
            [btn("❌ Скасувати", "adm_menu")],
        ]
    )


def dates_picker_kb(
    prefix: str, back_data: str, *, days: int = 28
) -> InlineKeyboardMarkup:
    """Кнопки найближчих днів. callback = prefix + ISO-дата."""
    start = today_local()
    dates = [start + timedelta(days=i) for i in range(days)]
    rows = [
        [btn(fmt_date_short(d), f"{prefix}{d.isoformat()}") for d in chunk]
        for chunk in chunked(dates, 4)
    ]
    rows.append([back(back_data)])
    return kb(rows)


# ---------- Заявки / записи ----------


def booking_actions_kb(booking: Booking) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if booking.status == BookingStatus.PENDING:
        rows.append(
            [
                btn("✅ Підтвердити", f"abk_ok_{booking.id}"),
                btn("❌ Відхилити", f"abk_no_{booking.id}"),
            ]
        )
    elif booking.status == BookingStatus.CONFIRMED:
        rows.append([btn("🚫 Скасувати запис", f"abk_cancel_{booking.id}")])
        if datetime.combine(booking.date, booking.start_time) <= now_local().replace(tzinfo=None):
            rows.append([btn("🙈 Клієнт не прийшов", f"abk_ns_{booking.id}")])
    if booking.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
        rows.append([btn("📅 Додати в мій календар", f"abk_ics_{booking.id}")])
    rows.append([btn("📞 Телефон клієнта", f"abk_call_{booking.id}")])
    from shared import undo

    if undo.available(booking.id) is not None:
        rows.append([btn("↩️ Скасувати останню дію", f"abk_undo_{booking.id}")])
    if booking.status == BookingStatus.CONFIRMED:
        rows.append(
            [btn("↩️ Зняти позначку «оплачено»" if booking.is_paid else "💰 Позначити оплаченим", f"abk_paid_{booking.id}")]
        )
    rows.append([btn("🏠 Меню", "adm_menu")])
    return kb(rows)


def reject_reason_kb(booking_id: int) -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("Без причини", f"abk_skip_{booking_id}")],
            [back(f"abk_view_{booking_id}")],
        ]
    )


def cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("Так, скасувати", f"abk_cancelyes_{booking_id}")],
            [back(f"abk_view_{booking_id}")],
        ]
    )


def day_bookings_kb(bookings: list[Booking], day: date) -> InlineKeyboardMarkup:
    rows = [
        [
            btn(
                f"{fmt_time(b.start_time)} · {b.client_name[:18]} · {b.title[:20]}",
                f"abk_view_{b.id}",
            )
        ]
        for b in bookings
    ]
    rows.append([btn("📅 Весь день в мій календар", f"abk_dayics_{day.isoformat()}")])
    rows.append([back("abk_days")])
    return kb(rows)


# ---------- Графік ----------


def cities_kb(cities: list[City], prefix: str, back_data: str = "adm_menu") -> InlineKeyboardMarkup:
    rows = [[btn(c.name, f"{prefix}{c.id}")] for c in cities]
    rows.append([back(back_data)])
    return kb(rows)


def schedule_city_kb(city_id: int) -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("🗓 Тижневий графік", f"asc_week_{city_id}")],
            [btn("📌 Виняток на дату", f"asc_exc_{city_id}")],
            [btn("📋 Список винятків", f"asc_list_{city_id}")],
            [btn("⏸ Заблокувати час (обидва міста)", "asc_blk")],
            [btn("📋 Заблокований час", "asc_blklist")],
            [back("asc_menu")],
        ]
    )


def week_kb(city_id: int, weekly: dict[int, Schedule]) -> InlineKeyboardMarkup:
    rows = []
    for wd in range(7):
        s = weekly.get(wd)
        if s is None or s.is_day_off:
            label = f"{WEEKDAYS_SHORT[wd]} · вихідний"
        else:
            label = f"{WEEKDAYS_SHORT[wd]} · {fmt_hours(s.start_time, s.end_time)}"
        rows.append([btn(label, f"asc_wd_{city_id}_{wd}")])
    rows.append([back(f"asc_city_{city_id}")])
    return kb(rows)


def weekday_kb(city_id: int, weekday: int, is_off: bool) -> InlineKeyboardMarkup:
    toggle = "✅ Зробити робочим" if is_off else "🚫 Зробити вихідним"
    return kb(
        [
            [btn("✏️ Змінити години", f"asc_wdhours_{city_id}_{weekday}")],
            [btn(toggle, f"asc_wdtoggle_{city_id}_{weekday}")],
            [back(f"asc_week_{city_id}")],
        ]
    )


def exception_day_kb(city_id: int, day: date, has_exception: bool) -> InlineKeyboardMarkup:
    iso = day.isoformat()
    rows = [
        [btn("🚫 Вихідний на цю дату", f"asc_excoff_{city_id}_{iso}")],
        [btn("✏️ Інші години на цю дату", f"asc_exchours_{city_id}_{iso}")],
    ]
    if has_exception:
        rows.append([btn("↩️ Прибрати виняток", f"asc_excdel_{city_id}_{iso}")])
    rows.append([back(f"asc_exc_{city_id}")])
    return kb(rows)


def exceptions_list_kb(
    city_id: int, exceptions: list[ScheduleException]
) -> InlineKeyboardMarkup:
    rows = []
    for e in exceptions:
        what = "вихідний" if e.is_day_off else fmt_hours(e.start_time, e.end_time)
        rows.append(
            [btn(f"❌ {fmt_date_short(e.date)} · {what}", f"asc_excdel_{city_id}_{e.date.isoformat()}")]
        )
    rows.append([back(f"asc_city_{city_id}")])
    return kb(rows)


def blocked_list_kb(slots: list[BlockedSlot], back_data: str) -> InlineKeyboardMarkup:
    rows = [
        [
            btn(
                f"❌ {fmt_date_short(s.date)} {fmt_hours(s.start_time, s.end_time)}"
                + (f" · {s.note[:15]}" if s.note else ""),
                f"asc_blkdel_{s.id}",
            )
        ]
        for s in slots
    ]
    rows.append([back(back_data)])
    return kb(rows)


# ---------- Послуги ----------


def categories_admin_kb(categories: list[ServiceCategory]) -> InlineKeyboardMarkup:
    rows = [
        [btn(("" if c.is_active else "🙈 ") + c.name, f"asv_cat_{c.id}")]
        for c in categories
    ]
    rows.append([btn("➕ Нова категорія", "asv_newcat")])
    rows.append([back("adm_menu")])
    return kb(rows)


def services_admin_kb(services: list[Service], category_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            btn(
                ("" if s.is_active else "🙈 ")
                + f"{s.name} · {s.duration_minutes} хв · {fmt_price(s.price)}",
                f"asv_svc_{s.id}",
            )
        ]
        for s in services
    ]
    rows.append([btn("➕ Нова послуга", f"asv_new_{category_id}")])
    rows.append([back("asv_menu")])
    return kb(rows)


def service_admin_kb(service: Service) -> InlineKeyboardMarkup:
    toggle = "🙈 Сховати від клієнтів" if service.is_active else "👁 Показувати клієнтам"
    return kb(
        [
            [btn("💰 Змінити ціну", f"asv_price_{service.id}")],
            [btn("🔥 Акційна ціна", f"asv_sale_{service.id}")],
            [btn("⏱ Змінити тривалість", f"asv_dur_{service.id}")],
            [btn("✏️ Змінити назву", f"asv_name_{service.id}")],
            [btn("📄 Опис для клієнтів", f"asv_desc_{service.id}")],
            [btn("🌿 Рекомендації й підготовка", f"acare_{service.id}")],
            [btn(toggle, f"asv_toggle_{service.id}")],
            [back(f"asv_cat_{service.category_id}")],
        ]
    )


def cancel_input_kb(back_data: str) -> InlineKeyboardMarkup:
    return kb([[btn("✖️ Скасувати", back_data)]])


# ---------- Налаштування ----------


def card_kb() -> InlineKeyboardMarkup:
    return kb(
        [
            [btn("Номер картки", "ast_cardedit_card_number")],
            [btn("Власник", "ast_cardedit_card_holder")],
            [btn("Банк", "ast_cardedit_bank_name")],
            [btn("Коментар", "ast_cardedit_comment")],
            [back("adm_menu")],
        ]
    )


def admins_kb(admins: list[AdminUser]) -> InlineKeyboardMarkup:
    rows = []
    for a in admins:
        if a.is_superadmin:
            rows.append([btn(f"👑 {a.telegram_id}", "ast_noop")])
        else:
            rows.append([btn(f"❌ Прибрати {a.telegram_id}", f"ast_adm_del_{a.telegram_id}")])
    rows.append([btn("➕ Додати адміністратора", "ast_adm_add")])
    rows.append([back("adm_menu")])
    return kb(rows)


async def menu_counts() -> dict:
    """Лічильники для кнопок головного меню. Помилка не ламає меню."""
    try:
        from sqlalchemy import func, select

        from shared.database import session_scope
        from shared.models import AdminTask, Booking, BookingStatus

        async with session_scope() as session:
            new = (
                await session.execute(
                    select(func.count()).select_from(Booking).where(Booking.status == BookingStatus.PENDING)
                )
            ).scalar_one()
            today = (
                await session.execute(
                    select(func.count()).select_from(Booking).where(
                        Booking.date == today_local(),
                        Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
                    )
                )
            ).scalar_one()
            tasks = (
                await session.execute(
                    select(func.count()).select_from(AdminTask).where(AdminTask.done.is_(False))
                )
            ).scalar_one()
        return {"new": new, "today": today, "tasks": tasks}
    except Exception:
        return {}
