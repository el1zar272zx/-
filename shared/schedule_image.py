# shared/schedule_image.py — розклад на 7 днів однією картинкою (PNG)
from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Sequence

from .models import Booking, BookingStatus
from .utils import WEEKDAYS_SHORT, minutes

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
    "arial.ttf",
)
_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
)


def _font(candidates, size):
    from PIL import ImageFont

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return (text + "…") if text else ""


def render_week(bookings: Sequence[Booking], first_day: date, days: int = 7) -> bytes:
    from PIL import Image, ImageDraw

    active = [b for b in bookings if b.status in (BookingStatus.CONFIRMED, BookingStatus.PENDING)]
    start_h = 9
    end_h = 20
    for b in active:
        start_h = min(start_h, b.start_time.hour)
        end_h = max(end_h, (minutes(b.end_time) + 59) // 60)
    end_h = min(max(end_h, start_h + 4), 24)

    left, top, col_w, hour_h = 64, 124, 210, 64
    width = left + col_w * days + 16
    height = top + (end_h - start_h) * hour_h + 24
    img = Image.new("RGB", (width, height), "#f6f8f7")
    d = ImageDraw.Draw(img)
    f_title, f_head = _font(_BOLD_CANDIDATES, 26), _font(_BOLD_CANDIDATES, 17)
    f_text, f_small = _font(_FONT_CANDIDATES, 14), _font(_FONT_CANDIDATES, 13)

    last_day = first_day + timedelta(days=days - 1)
    d.text(
        (16, 18),
        f"Розклад: {first_day:%d.%m} — {last_day:%d.%m.%Y}",
        font=f_title,
        fill="#14231e",
    )
    d.text((16, 56), "зелений — підтверджено · жовтий — очікує підтвердження", font=f_small, fill="#5b6b64")

    for h in range(start_h, end_h + 1):
        y = top + (h - start_h) * hour_h
        d.line([(left - 6, y), (width - 12, y)], fill="#d5dfda")
        d.text((12, y - 8), f"{h:02d}:00", font=f_small, fill="#5b6b64")

    today = date.today()
    for i in range(days):
        day = first_day + timedelta(days=i)
        x = left + i * col_w
        head_fill = "#0f7b5f" if day == today else "#e4ece8"
        head_text = "#ffffff" if day == today else "#14231e"
        d.rounded_rectangle([x + 3, top - 38, x + col_w - 3, top - 8], radius=8, fill=head_fill)
        d.text((x + 12, top - 33), f"{WEEKDAYS_SHORT[day.weekday()]} {day:%d.%m}", font=f_head, fill=head_text)
        d.line([(x, top), (x, height - 24)], fill="#d5dfda")
        for b in active:
            if b.date != day:
                continue
            y1 = top + (minutes(b.start_time) - start_h * 60) * hour_h / 60
            y2 = top + (minutes(b.end_time) - start_h * 60) * hour_h / 60
            ok = b.status == BookingStatus.CONFIRMED
            d.rounded_rectangle(
                [x + 5, y1 + 2, x + col_w - 5, max(y2 - 2, y1 + 22)],
                radius=8,
                fill="#cfeedf" if ok else "#fbe9b7",
                outline="#0f7b5f" if ok else "#c99a1e",
            )
            tx = x + 11
            inner = col_w - 24
            d.text((tx, y1 + 6), f"{b.start_time:%H:%M}–{b.end_time:%H:%M}", font=f_small, fill="#14231e")
            if y2 - y1 >= 44:
                d.text((tx, y1 + 22), _fit(d, b.client_name, f_text, inner), font=f_text, fill="#14231e")
            if y2 - y1 >= 62:
                d.text((tx, y1 + 40), _fit(d, b.title, f_small, inner), font=f_small, fill="#3c4d46")
    d.line([(left + days * col_w, top), (left + days * col_w, height - 24)], fill="#d5dfda")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=82, optimize=True)  # легка картинка: швидко вантажиться
    return buf.getvalue()
