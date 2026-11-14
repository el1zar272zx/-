# shared/calendar_utils.py
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from typing import Iterable

from .models import Booking, BookingStatus
from .utils import get_tz


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    """RFC 5545: рядки довші за 75 байт переносяться (iPhone цього вимагає)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out: list[str] = []
    cur = b""
    limit = 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur = b" "  # продовження починається з пробілу
            limit = 75
        cur += b
    if cur.strip():
        out.append(cur.decode("utf-8"))
    return out


CAL_HOWTO = (
    "📅 Як додати в календар:\n"
    "• iPhone: натисніть на файл і оберіть «Додати все» / «Календар» "
    "(або «Поділитися» → «Календар»).\n"
    "• Android: відкрийте файл і оберіть «Google Календар», або скористайтеся кнопкою нижче."
)


def google_calendar_url(booking: Booking, *, for_admin: bool = False) -> str:
    """Посилання «додати в Google Календар»: працює на Android і в браузері iPhone."""
    start = datetime.combine(booking.date, booking.start_time)
    end = datetime.combine(booking.date, booking.end_time)
    title = f"{booking.client_name} — {booking.title}" if for_admin else booking.title
    details = (
        f"Телефон: {booking.client_phone}" if for_admin else f"Послуга: {booking.title}"
    )
    return (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={quote(title)}"
        f"&dates={_to_utc_str(start)}/{_to_utc_str(end)}"
        f"&details={quote(details)}"
    )


def _to_utc_str(local_dt: datetime) -> str:
    """Місцевий час (за TIMEZONE) -> UTC у форматі iCalendar. VTIMEZONE не потрібен."""
    aware = local_dt.replace(tzinfo=get_tz())
    return aware.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _vevent(booking: Booking, *, for_admin: bool, now_utc: str) -> list[str]:
    start = datetime.combine(booking.date, booking.start_time)
    end = datetime.combine(booking.date, booking.end_time)

    if for_admin:
        summary = f"{booking.client_name} — {booking.title}"
        description = (
            f"Клієнт: {booking.client_name}\nТелефон: {booking.client_phone}\n"
            f"Послуга: {booking.title}\nМісто: {booking.city.name}\n"
            f"Статус: {'підтверджено' if booking.status == BookingStatus.CONFIRMED else 'очікує підтвердження'}"
        )
        status = (
            "CONFIRMED" if booking.status == BookingStatus.CONFIRMED else "TENTATIVE"
        )
    else:
        summary = f"{booking.title} ({booking.city.name})"
        description = f"Послуга: {booking.title}\nМісто: {booking.city.name}"
        status = "CONFIRMED"

    return [
        "BEGIN:VEVENT",
        f"UID:booking-{booking.id}@massage-salon",
        f"DTSTAMP:{now_utc}",
        f"DTSTART:{_to_utc_str(start)}",
        f"DTEND:{_to_utc_str(end)}",
        f"SUMMARY:{_ics_escape(summary)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"STATUS:{status}",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:Нагадування про запис",
        "TRIGGER:-PT60M",
        "END:VALARM",
        "END:VEVENT",
    ]


def generate_ics_for_bookings(
    bookings: Iterable[Booking], *, for_admin: bool = False
) -> bytes:
    """Один .ics з усіма записами: відкриваєте файл — і все в календарі."""
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Massage Salon Bot//UA",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Записи",
    ]
    for b in bookings:
        lines += _vevent(b, for_admin=for_admin, now_utc=now_utc)
    lines.append("END:VCALENDAR")
    folded: list[str] = []
    for ln in lines:
        folded += _fold(ln)
    return ("\r\n".join(folded) + "\r\n").encode("utf-8")


def generate_ics_bytes(booking: Booking, *, for_admin: bool = False) -> bytes:
    """Вміст .ics для одного запису."""
    return generate_ics_for_bookings([booking], for_admin=for_admin)
