# shared/reports.py — звіти для адміністратора, статистика, експорт у CSV
from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import date, timedelta

from .models import Booking, BookingStatus, Client
from .utils import WEEKDAYS_SHORT, esc, fmt_date_short, fmt_price

_DONE = (BookingStatus.CONFIRMED,)


def _bar(value: int, top: int, width: int = 10) -> str:
    if top <= 0:
        return ""
    return "█" * max(1 if value else 0, round(width * value / top))


def build_report(bookings: list[Booking], first: date, last: date, title: str) -> str:
    done = [b for b in bookings if b.status in _DONE]
    paid = [b for b in done if b.is_paid]
    canceled = [b for b in bookings if b.status == BookingStatus.CANCELED]
    no_show = [b for b in bookings if b.status == BookingStatus.NO_SHOW]
    revenue_expected = sum(b.price or 0 for b in done)
    revenue_paid = sum(b.price or 0 for b in paid)
    clients = {b.client_telegram_id for b in done}

    lines = [
        f"📊 <b>{title}</b>",
        f"{fmt_date_short(first)} — {fmt_date_short(last)}",
        "",
        f"Підтверджених записів: <b>{len(done)}</b>",
        f"Унікальних клієнтів: <b>{len(clients)}</b>",
        f"Сума за записами: <b>{fmt_price(revenue_expected)}</b>",
        f"З них позначено оплаченими: <b>{fmt_price(revenue_paid)}</b>",
        f"Скасовано: {len(canceled)} · Не прийшли: {len(no_show)}",
    ]
    if done:
        top = Counter(b.title for b in done).most_common(3)
        lines += ["", "<b>Популярні послуги</b>"]
        lines += [f"• {esc(name)} — {n}" for name, n in top]
    return "\n".join(lines)


def build_stats(bookings: list[Booking]) -> str:
    """Завантаженість за днями тижня та годинами (підтверджені записи)."""
    done = [b for b in bookings if b.status in _DONE]
    if not done:
        return "Поки що немає підтверджених записів для статистики."
    by_day = Counter(b.date.weekday() for b in done)
    by_hour = Counter(b.start_time.hour for b in done)
    top_day = max(by_day.values())
    lines = ["📈 <b>Завантаженість</b>", "", "<b>За днями тижня</b>"]
    for wd in range(7):
        n = by_day.get(wd, 0)
        lines.append(f"{WEEKDAYS_SHORT[wd]}  {_bar(n, top_day)} {n}")
    top_hour = max(by_hour.values())
    lines += ["", "<b>За годинами</b>"]
    for h in sorted(by_hour):
        lines.append(f"{h:02d}:00  {_bar(by_hour[h], top_hour)} {by_hour[h]}")
    quiet = [WEEKDAYS_SHORT[wd] for wd in range(7) if by_day.get(wd, 0) == 0]
    if quiet:
        lines += ["", "Дні без записів: " + ", ".join(quiet)]
    return "\n".join(lines)


def bookings_csv(bookings: list[Booking]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(
        ["ID", "Дата", "Початок", "Кінець", "Послуга", "Клієнт", "Телефон", "Ціна", "Оплата", "Оплачено", "Статус"]
    )
    for b in bookings:
        w.writerow(
            [
                b.id,
                b.date.isoformat(),
                b.start_time.strftime("%H:%M"),
                b.end_time.strftime("%H:%M"),
                b.title,
                b.client_name,
                b.client_phone,
                b.price if b.price is not None else "",
                b.payment_method.value,
                "так" if b.is_paid else "ні",
                b.status.value,
            ]
        )
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel правильно читає кирилицю


def clients_csv(clients: list[Client]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Імʼя", "Телефон", "Дата народження", "Мова", "Нотатка", "Анкета"])
    for c in clients:
        w.writerow(
            [
                c.name,
                c.phone,
                c.birthday.strftime("%d.%m") if c.birthday else "",
                c.lang,
                c.notes or "",
                c.intake or "",
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def week_range(today: date) -> tuple[date, date]:
    """Попередній повний тиждень (пн–нд)."""
    monday = today - timedelta(days=today.weekday())
    return monday - timedelta(days=7), monday - timedelta(days=1)
