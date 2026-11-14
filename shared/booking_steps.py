# shared/booking_steps.py — статус заявки по кроках для клієнта
from __future__ import annotations

from .models import Booking, BookingStatus, PaymentMethod


def steps_text(booking: Booking) -> str:
    """Короткий трекер: заявку отримано → підтвердження майстра → нагадування (→ оплата)."""
    st = booking.status
    if st == BookingStatus.CANCELED:
        return "📍 <b>Статус заявки</b>\n✅ Заявку отримано\n❌ Запис скасовано"
    if st == BookingStatus.REJECTED:
        return "📍 <b>Статус заявки</b>\n✅ Заявку отримано\n❌ Майстер не змогла підтвердити цей час. Оберіть інший у меню «Записатися»"
    if st == BookingStatus.NO_SHOW:
        return "📍 <b>Статус заявки</b>\n✅ Заявку отримано\n✅ Підтверджено майстром\n➖ Візит не відбувся"
    lines = ["📍 <b>Статус заявки</b>", "✅ Заявку отримано"]
    if st == BookingStatus.PENDING:
        lines.append("⏳ Очікує підтвердження майстра (зазвичай це швидко)")
        lines.append("⚪ Нагадаємо про візит за добу й за 2 години")
    else:
        lines.append("✅ Підтверджено майстром")
        lines.append("✅ Ви підтвердили візит" if booking.client_confirmed else "🔔 Нагадаємо про візит за добу й за 2 години")
    if booking.payment_method == PaymentMethod.CARD:
        lines.append("✅ Оплачено" if booking.is_paid else "💳 Оплата карткою: очікується")
    return "\n".join(lines)
