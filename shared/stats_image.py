# shared/stats_image.py — статистика за період однією картинкою (PNG)
from __future__ import annotations

import io
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Sequence

from .models import Booking, BookingStatus
from .schedule_image import _BOLD_CANDIDATES, _FONT_CANDIDATES, _font
from .utils import WEEKDAYS_SHORT, today_local

BG, CARD, INK, MUTED, ACC, ACC2 = "#f6f8f7", "#ffffff", "#14231e", "#5b6b64", "#0f7b5f", "#7fc8ad"


def compute(bookings: Sequence[Booking], today: date) -> dict:
    done = [b for b in bookings if b.status == BookingStatus.CONFIRMED and b.date <= today]
    revenue = sum(b.price or 0 for b in done)
    by_wd = Counter(b.date.weekday() for b in done)
    weekly: dict[date, int] = defaultdict(int)
    for b in done:
        weekly[b.date - timedelta(days=b.date.weekday())] += b.price or 0
    services: dict[str, int] = defaultdict(int)
    for b in done:
        services[b.title or "—"] += b.price or 0
    visits_by_client = Counter(b.client_telegram_id for b in done)
    clients = len(visits_by_client)
    returning = sum(1 for v in visits_by_client.values() if v >= 2)
    return {
        "visits": len(done),
        "revenue": revenue,
        "avg": revenue // len(done) if done else 0,
        "clients": clients,
        "returning_pct": round(returning * 100 / clients) if clients else 0,
        "by_wd": [by_wd.get(i, 0) for i in range(7)],
        "weekly": sorted(weekly.items()),
        "top": sorted(services.items(), key=lambda kv: kv[1], reverse=True)[:5],
    }


def _bars(d, x, y, w, h, labels, values, f_small, color=ACC):
    top = max(values) if values and max(values) > 0 else 1
    n = max(len(values), 1)
    slot = w / n
    base = y + h - 22
    for i, (lab, v) in enumerate(zip(labels, values)):
        bh = int((h - 50) * v / top)
        x0 = int(x + i * slot + slot * 0.18)
        x1 = int(x + (i + 1) * slot - slot * 0.18)
        d.rounded_rectangle([x0, base - bh, x1, base], radius=5, fill=color if v else "#e4ece8")
        d.text(((x0 + x1) / 2, base + 4), lab, font=f_small, fill=MUTED, anchor="ma")
        if v:
            d.text(((x0 + x1) / 2, base - bh - 4), str(v), font=f_small, fill=INK, anchor="md")


def render_stats(bookings: Sequence[Booking], first: date, last: date) -> bytes:
    from PIL import Image, ImageDraw

    s = compute(bookings, today_local())
    W, H = 1200, 880
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    f_title, f_big = _font(_BOLD_CANDIDATES, 30), _font(_BOLD_CANDIDATES, 34)
    f_head, f_text, f_small = _font(_BOLD_CANDIDATES, 19), _font(_FONT_CANDIDATES, 17), _font(_FONT_CANDIDATES, 14)

    d.text((32, 24), "Статистика", font=f_title, fill=INK)
    d.text((32, 66), f"{first:%d.%m.%Y} – {last:%d.%m.%Y}", font=f_text, fill=MUTED)

    kpis = [
        ("Візитів", str(s["visits"])),
        ("Дохід, грн", f"{s['revenue']:,}".replace(",", " ")),
        ("Середній чек", f"{s['avg']} грн"),
        ("Клієнтів", str(s["clients"])),
        ("Повернулись", f"{s['returning_pct']}%"),
    ]
    kw = (W - 64 - 16 * 4) // 5
    for i, (lab, val) in enumerate(kpis):
        x = 32 + i * (kw + 16)
        d.rounded_rectangle([x, 108, x + kw, 208], radius=14, fill=CARD, outline="#d5dfda")
        d.text((x + 18, 122), lab, font=f_text, fill=MUTED)
        d.text((x + 18, 154), val, font=f_big, fill=ACC)

    def panel(x, y, w, h, title):
        d.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=CARD, outline="#d5dfda")
        d.text((x + 18, y + 14), title, font=f_head, fill=INK)

    panel(32, 228, 560, 300, "Візити за днями тижня")
    _bars(d, 50, 262, 524, 256, list(WEEKDAYS_SHORT), s["by_wd"], f_small)

    weekly = s["weekly"][-8:]
    panel(608, 228, 560, 300, "Дохід по тижнях, грн")
    if weekly:
        _bars(d, 626, 262, 524, 256, [f"{k:%d.%m}" for k, _ in weekly], [v for _, v in weekly], f_small, ACC2)
    else:
        d.text((626, 380), "Поки немає даних", font=f_text, fill=MUTED)

    panel(32, 548, W - 64, 300, "Найприбутковіші послуги")
    top = s["top"]
    if top:
        mx = max(v for _, v in top) or 1
        for i, (name, val) in enumerate(top):
            y = 596 + i * 48
            label = name if len(name) <= 38 else name[:37] + "…"
            d.text((52, y), label, font=f_text, fill=INK)
            bw = int(560 * val / mx)
            d.rounded_rectangle([420, y - 2, 420 + max(bw, 6), y + 26], radius=6, fill=ACC)
            d.text((420 + max(bw, 6) + 10, y), f"{val} грн", font=f_text, fill=MUTED)
    else:
        d.text((52, 640), "Поки немає завершених візитів за цей період", font=f_text, fill=MUTED)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()
