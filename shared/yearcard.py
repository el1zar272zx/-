# shared/yearcard.py — «Ваш рік з нами»: підсумок візитів клієнта за рік (текст і картинка)
from __future__ import annotations

import io
import logging
from collections import Counter
from datetime import datetime

from sqlalchemy import select

from .database import session_scope
from .models import Booking, BookingStatus
from .schedule_image import _BOLD_CANDIDATES, _FONT_CANDIDATES, _font
from .utils import now_local

logger = logging.getLogger(__name__)

MONTHS_UA = ["січні", "лютому", "березні", "квітні", "травні", "червні", "липні", "серпні", "вересні", "жовтні", "листопаді", "грудні"]
MONTHS_RU = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"]


async def stats(tid: int, year: int) -> dict | None:
    now = now_local().replace(tzinfo=None)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Booking).where(
                    Booking.client_telegram_id == tid,
                    Booking.status == BookingStatus.CONFIRMED,
                )
            )
        ).scalars().all()
    done = [b for b in rows if b.date.year == year and datetime.combine(b.date, b.end_time) <= now]
    if not done:
        return None
    minutes = sum(
        (b.end_time.hour * 60 + b.end_time.minute) - (b.start_time.hour * 60 + b.start_time.minute) for b in done
    )
    top_service = Counter(b.title for b in done).most_common(1)[0][0]
    top_month = Counter(b.date.month for b in done).most_common(1)[0][0]
    return {
        "year": year,
        "visits": len(done),
        "hours": round(minutes / 60, 1),
        "top_service": top_service,
        "top_month": top_month,
        "first": min(b.date for b in done),
    }


def _hours_str(h: float) -> str:
    return str(int(h)) if abs(h - int(h)) < 0.05 else f"{h:.1f}"


def caption(st: dict, name: str, lang: str = "uk") -> str:
    ru = lang == "ru"
    months = MONTHS_RU if ru else MONTHS_UA
    hrs = _hours_str(st["hours"])
    if ru:
        return (
            f"🎁 <b>Ваш {st['year']} год с нами</b>\n\n"
            f"✨ Визитов: <b>{st['visits']}</b>\n"
            f"⏱ Часов заботы о себе: <b>{hrs}</b>\n"
            f"💆 Любимая процедура: <b>{st['top_service']}</b>\n"
            f"📅 Чаще всего вы приходили в {months[st['top_month'] - 1]}\n\n"
            f"Спасибо, {name}, что вы с нами 💛"
        )
    return (
        f"🎁 <b>Ваш {st['year']} рік з нами</b>\n\n"
        f"✨ Візитів: <b>{st['visits']}</b>\n"
        f"⏱ Годин турботи про себе: <b>{hrs}</b>\n"
        f"💆 Улюблена процедура: <b>{st['top_service']}</b>\n"
        f"📅 Найчастіше ви приходили у {months[st['top_month'] - 1]}\n\n"
        f"Дякуємо, {name}, що ви з нами 💛"
    )


def _wrap(d, text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render(st: dict, name: str, brand: str, lang: str = "uk") -> bytes:
    from PIL import Image, ImageDraw

    ru = lang == "ru"
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), "#0f7b5f")
    d = ImageDraw.Draw(img)
    top, bot = (15, 123, 95), (84, 168, 139)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([W - 460, -220, W + 200, 440], fill=(255, 255, 255, 46))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    d = ImageDraw.Draw(img)
    f_big, f_mid, f_sm, f_num = (
        _font(_BOLD_CANDIDATES, 74),
        _font(_FONT_CANDIDATES, 38),
        _font(_FONT_CANDIDATES, 32),
        _font(_BOLD_CANDIDATES, 150),
    )
    d.text((80, 90), brand[:34], font=f_sm, fill="#eafaf4")
    d.text((80, 190), f"{st['year']}", font=f_num, fill="#ffffff")
    d.text((80, 370), "ваш рік з нами" if not ru else "ваш год с нами", font=f_big, fill="#ffffff")
    y = 540
    items = [
        (str(st["visits"]), "візитів" if not ru else "визитов"),
        (_hours_str(st["hours"]), "годин для себе" if not ru else "часов для себя"),
    ]
    x = 80
    for num, label in items:
        d.rounded_rectangle([x, y, x + 440, y + 230], radius=36, fill="#ffffff")
        d.text((x + 36, y + 24), num, font=_font(_BOLD_CANDIDATES, 110), fill="#0f7b5f")
        d.text((x + 36, y + 160), label, font=f_sm, fill="#5b6b64")
        x += 480
    y = 820
    d.text((80, y), "Улюблена процедура" if not ru else "Любимая процедура", font=f_sm, fill="#eafaf4")
    for i, line in enumerate(_wrap(d, st["top_service"], f_big, W - 160)[:3]):
        d.text((80, y + 54 + i * 88), line, font=f_big, fill="#ffffff")
    d.text((80, H - 190), (f"{name}, дякуємо, що ви з нами" if not ru else f"{name}, спасибо, что вы с нами")[:60], font=f_mid, fill="#ffffff")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
