# shared/notifications_soft.py — тексти повідомлень (HTML parse mode)
from __future__ import annotations

from .models import Booking, BookingStatus, CardSettings, PaymentMethod, Review
from .utils import esc, fmt_date_long, fmt_hours, fmt_price, fmt_time

STATUS_UA = {
    BookingStatus.PENDING: "⏳ Очікує підтвердження",
    BookingStatus.CONFIRMED: "✅ Підтверджено",
    BookingStatus.REJECTED: "❌ Відхилено",
    BookingStatus.CANCELED: "🚫 Скасовано",
    BookingStatus.NO_SHOW: "🙈 Не прийшов",
}

PAYMENT_UA = {
    PaymentMethod.CASH: "💵 Готівка",
    PaymentMethod.CARD: "💳 Картка",
}


def format_booking_card(
    booking: Booking,
    *,
    for_admin: bool = False,
    visits: int | None = None,
    no_shows: int | None = None,
    call_ok: bool = False,
) -> str:
    """Картка запису. Для адміна додається ім'я та телефон клієнта."""
    lines = [
        f"<b>Запис #{booking.id}</b> — {STATUS_UA[booking.status]}",
        "",
        f"💆 Послуга: {esc(booking.title)}",
        f"📍 Місто: {esc(booking.city.name)}",
        f"📅 Дата: {fmt_date_long(booking.date)}",
        f"🕐 Час: {fmt_hours(booking.start_time, booking.end_time)}",
        f"💰 Вартість: <b>{fmt_price(booking.price)}</b>",
        f"💳 Оплата: {PAYMENT_UA[booking.payment_method]}",
    ]
    if for_admin:
        lines += [
            "",
            f"👤 Клієнт: {esc(booking.client_name)}",
            f"📞 Телефон: {esc(booking.client_phone)}",
        ]
        if visits is not None:
            lines.append(
                "Новий клієнт" if visits == 0 else f"Завершених візитів: {visits}"
            )
    if for_admin and call_ok:
        lines.append("📞 Клієнт просить дзвонити")
    if for_admin and getattr(booking, "wish", None):
        lines.append(f"✍️ Побажання: {esc(booking.wish)}")
    if for_admin and getattr(booking, "flags", None):
        lines.append(f"🚩 Увага: {esc(booking.flags)}")
    if for_admin and no_shows:
        lines.append(f"⚠️ Неявок раніше: {no_shows}")
    if for_admin and booking.is_paid:
        lines.append("💰 Оплачено")
    if for_admin and booking.client_confirmed:
        lines.append("✅ Клієнт підтвердив візит")
    if booking.cancel_reason and booking.status in (
        BookingStatus.REJECTED,
        BookingStatus.CANCELED,
    ):
        lines += ["", f"Причина: {esc(booking.cancel_reason)}"]
    return "\n".join(lines)


def format_card_settings_text(cs: CardSettings | None) -> str:
    if cs is None:
        return "Реквізити ще не налаштовані."
    parts: list[str] = []
    if cs.card_number:
        parts.append(f"Номер картки: <code>{esc(cs.card_number)}</code>")
    if cs.card_holder:
        parts.append(f"Власник: {esc(cs.card_holder)}")
    if cs.bank_name:
        parts.append(f"Банк: {esc(cs.bank_name)}")
    if cs.comment:
        parts.append(f"\n{esc(cs.comment)}")
    return "\n".join(parts) if parts else "Реквізити ще не налаштовані."


def format_booking_created_for_client(
    booking: Booking, card: CardSettings | None = None
) -> str:
    text = (
        "🌿 <b>Заявку прийнято</b>\n"
        "Дякуємо! Майстер підтвердить її найближчим часом, і ви отримаєте повідомлення.\n\n"
        + format_booking_card(booking)
    )
    if booking.payment_method == PaymentMethod.CARD:
        text += "\n\n<b>Реквізити для оплати:</b>\n" + format_card_settings_text(card)
    return text


def format_confirmed_for_client(booking: Booking) -> str:
    from .salon import address_block

    return (
        "✅ <b>Запис підтверджено</b>\nЧекаємо на вас 💛\n\n"
        + format_booking_card(booking)
        + address_block()
    )


def format_rejected_for_client(booking: Booking) -> str:
    return (
        "🙏 <b>Запис не вдалося підтвердити</b>\nНа жаль, цей час недоступний.\n\n"
        + format_booking_card(booking)
        + "\n\nОберіть, будь ласка, інший час у меню «Записатися»."
    )


def format_canceled_by_admin_for_client(booking: Booking) -> str:
    return (
        "🙏 <b>Запис скасовано майстром</b>\nПробачте за незручність.\n\n"
        + format_booking_card(booking)
        + "\n\nОберіть, будь ласка, інший час у меню «Записатися»."
    )


def format_reminder(booking: Booking, hours_before: int) -> str:
    if hours_before >= 24 and hours_before % 24 == 0:
        when = "завтра" if hours_before == 24 else f"через {hours_before // 24} дн."
    else:
        when = f"через {hours_before} год."
    text = f"⏰ <b>Нагадування</b>\nВаш запис {when}\n\n" + format_booking_card(booking)
    from .salon import address_block

    text += address_block()
    return text


def format_rescheduled_for_admin(booking: Booking, old_date, old_start) -> str:
    return (
        "🔄 <b>Клієнт переніс запис</b>\n"
        f"Було: {fmt_date_long(old_date)}, {fmt_time(old_start)}\n\n"
        + format_booking_card(booking, for_admin=True)
    )


def format_admin_soon(
    booking: Booking,
    minutes: int,
    visits: int | None = None,
    no_shows: int | None = None,
    call_ok: bool = False,
) -> str:
    left = f"{minutes // 60} год." if minutes % 60 == 0 else f"{minutes} хв."
    return (
        f"⏰ <b>Клієнт буде через {left}</b>\n\n"
        + format_booking_card(
            booking, for_admin=True, visits=visits, no_shows=no_shows, call_ok=call_ok
        )
    )


def format_new_booking_for_admin(
    booking: Booking,
    *,
    visits: int | None = None,
    no_shows: int | None = None,
    call_ok: bool = False,
) -> str:
    return "🆕 <b>Нова заявка</b>\n\n" + format_booking_card(
        booking, for_admin=True, visits=visits, no_shows=no_shows, call_ok=call_ok
    )


def format_canceled_by_client_for_admin(booking: Booking) -> str:
    return "🚫 <b>Клієнт скасував запис</b>\n\n" + format_booking_card(
        booking, for_admin=True
    )


def format_review_request(booking: Booking) -> str:
    return (
        "💛 <b>Дякуємо, що були у нас!</b>\n"
        f"Як вам візит ({esc(booking.title)}, {fmt_date_long(booking.date)})?\n"
        "Оцініть, будь ласка:"
    )


def format_review_for_admin(review: Review, *, final: bool = False) -> str:
    b = review.booking
    stars = "⭐" * review.rating
    text = (
        f"{stars} <b>Новий відгук</b> ({review.rating}/5)\n"
        f"{esc(b.client_name)}, {esc(b.client_phone)}\n"
        f"{esc(b.title)} · {fmt_date_long(b.date)}"
    )
    if review.comment:
        text += f"\n\n«{esc(review.comment)}»"
    return text


def format_waitlist_free(day_text: str) -> str:
    return (
        f"🔔 На {day_text} звільнилося місце!\n"
        "Натисніть «📝 Записатися», щоб обрати час, поки його не зайняли."
    )


def format_digest(
    day,
    bookings: list[Booking],
    pending_count: int,
    *,
    firsts: set[int] | None = None,
    windows: list[tuple] | None = None,
    tomorrow: int = 0,
    birthdays: list[str] | None = None,
    notes_pending: int = 0,
) -> str:
    """Ранкова сводка для адмінів: день, перші візити, побажання, вільні вікна, що чекає."""
    firsts = firsts or set()
    active = [b for b in bookings if b.status in (BookingStatus.CONFIRMED, BookingStatus.PENDING)]
    minutes = sum(
        (b.end_time.hour * 60 + b.end_time.minute) - (b.start_time.hour * 60 + b.start_time.minute)
        for b in active
    )
    lines = [f"☀️ <b>Доброго ранку!</b> Сьогодні, {fmt_date_long(day)}", ""]
    if active:
        lines.append(f"Візитів: <b>{len(active)}</b> · у роботі близько {minutes // 60} год {minutes % 60:02d} хв")
        lines.append("")
        for b in active:
            mark = "✅" if b.status == BookingStatus.CONFIRMED else "⏳"
            first = " 🆕 вперше" if b.id in firsts else ""
            lines.append(
                f"{mark} {fmt_hours(b.start_time, b.end_time)} · {esc(b.title)} · "
                f"{esc(b.client_name)} ({esc(b.client_phone)}){first}"
            )
            if getattr(b, "wish", None):
                lines.append(f"    ✍️ {esc(b.wish)}")
    else:
        lines.append("Записів на сьогодні немає. Можна видихнути 🌿")
    if windows:
        lines += ["", "🕳 <b>Вільні вікна:</b> " + ", ".join(f"{a:%H:%M}–{z:%H:%M}" for a, z in windows)]
    if birthdays:
        lines += ["", "🎂 День народження: " + ", ".join(esc(x) for x in birthdays)]
    tail = []
    if pending_count:
        tail.append(f"📥 Заявок, що чекають підтвердження: <b>{pending_count}</b>")
    if notes_pending:
        tail.append(f"📝 Візитів без нотаток: {notes_pending}")
    if tomorrow:
        tail.append(f"📅 Завтра візитів: {tomorrow}")
    if tail:
        lines += [""] + tail
    return "\n".join(lines)
