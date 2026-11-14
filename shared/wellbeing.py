# shared/wellbeing.py — щоденник самопочуття: оцінка болю/напруги 1–10 і графік динаміки
from __future__ import annotations

import io
import logging
from statistics import mean

from sqlalchemy import select

from .database import session_scope
from .models import WellbeingEntry
from .schedule_image import _BOLD_CANDIDATES, _FONT_CANDIDATES, _font

logger = logging.getLogger(__name__)

KIND_COLOR = {"before": "#e0a030", "after": "#2f9e7a", "now": "#7a8a84"}
KIND_UA = {"before": "до візиту", "after": "після візиту", "now": "зараз"}
KIND_RU = {"before": "до визита", "after": "после визита", "now": "сейчас"}


async def add(tid: int, score: int, kind: str = "now", booking_id: int | None = None) -> None:
    score = max(1, min(10, int(score)))
    async with session_scope() as session:
        session.add(WellbeingEntry(client_telegram_id=tid, score=score, kind=kind, booking_id=booking_id))


async def recent(tid: int, limit: int = 20) -> list[WellbeingEntry]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(WellbeingEntry)
                .where(WellbeingEntry.client_telegram_id == tid)
                .order_by(WellbeingEntry.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    return list(reversed(rows))


async def has_entry(tid: int, booking_id: int, kind: str) -> bool:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(WellbeingEntry.id).where(
                    WellbeingEntry.client_telegram_id == tid,
                    WellbeingEntry.booking_id == booking_id,
                    WellbeingEntry.kind == kind,
                )
            )
        ).first()
    return row is not None


def deltas(entries: list[WellbeingEntry]) -> list[int]:
    """Різниця «після мінус до» для візитів, де є обидві оцінки (від'ємна = стало легше)."""
    before: dict[int, int] = {}
    after: dict[int, int] = {}
    for e in entries:
        if e.booking_id is None:
            continue
        (before if e.kind == "before" else after if e.kind == "after" else {})[e.booking_id] = e.score
    return [after[b] - before[b] for b in before if b in after]


def summary(entries: list[WellbeingEntry], lang: str = "uk") -> str:
    ru = lang == "ru"
    if not entries:
        return "Записів поки немає." if not ru else "Записей пока нет."
    last = entries[-1]
    kinds = KIND_RU if ru else KIND_UA
    lines = [
        (f"Останній запис: {last.score}/10 ({kinds.get(last.kind, '')}, {last.created_at:%d.%m})")
        if not ru
        else f"Последняя запись: {last.score}/10 ({kinds.get(last.kind, '')}, {last.created_at:%d.%m})"
    ]
    if len(entries) >= 3:
        avg = mean(e.score for e in entries)
        lines.append(f"Середнє: {avg:.1f}" if not ru else f"Среднее: {avg:.1f}")
    ds = deltas(entries)
    if ds:
        d = mean(ds)
        if d < 0:
            lines.append(
                f"Після візитів у середньому легше на {abs(d):.1f} бала 💚"
                if not ru
                else f"После визитов в среднем легче на {abs(d):.1f} балла 💚"
            )
        elif d > 0:
            lines.append(
                "Після візитів оцінка трохи вища: розкажіть про це майстру, вона підкаже, що змінити."
                if not ru
                else "После визитов оценка немного выше: расскажите об этом мастеру, она подскажет, что изменить."
            )
        else:
            lines.append("До й після візитів оцінки схожі." if not ru else "До и после визитов оценки похожи.")
    return "\n".join(lines)


def chart(entries: list[WellbeingEntry], lang: str = "uk") -> bytes:
    """Лінійний графік останніх оцінок (PNG)."""
    from PIL import Image, ImageDraw

    ru = lang == "ru"
    W, H = 980, 560
    img = Image.new("RGB", (W, H), "#f6f8f7")
    d = ImageDraw.Draw(img)
    f_t = _font(_BOLD_CANDIDATES, 34)
    f_s = _font(_FONT_CANDIDATES, 22)
    d.text((40, 24), "Динаміка самопочуття" if not ru else "Динамика самочувствия", font=f_t, fill="#14231e")
    d.text(
        (40, 70),
        "1 — майже нічого не турбує, 10 — дуже сильно" if not ru else "1 — почти ничего не беспокоит, 10 — очень сильно",
        font=f_s,
        fill="#5b6b64",
    )
    x0, y0, x1, y1 = 90, 120, W - 50, H - 90
    for v in range(1, 11):
        y = y1 - (y1 - y0) * (v - 1) / 9
        d.line([x0, y, x1, y], fill="#e4ece8", width=1)
        d.text((x0 - 38, y - 12), str(v), font=f_s, fill="#5b6b64")
    n = len(entries)
    pts = []
    for i, e in enumerate(entries):
        x = x0 + (x1 - x0) * (i / (n - 1) if n > 1 else 0.5)
        y = y1 - (y1 - y0) * (e.score - 1) / 9
        pts.append((x, y, e))
    if len(pts) > 1:
        d.line([(x, y) for x, y, _ in pts], fill="#9fb3aa", width=4)
    for x, y, e in pts:
        c = KIND_COLOR.get(e.kind, "#7a8a84")
        d.ellipse([x - 11, y - 11, x + 11, y + 11], fill=c, outline="#ffffff", width=3)
    # підписи дат: перша, середня, остання
    if pts:
        idxs = sorted({0, len(pts) // 2, len(pts) - 1})
        for i in idxs:
            x, _, e = pts[i]
            d.text((x - 32, y1 + 14), f"{e.created_at:%d.%m}", font=f_s, fill="#5b6b64")
    lx = x0
    for kind in ("before", "after", "now"):
        d.ellipse([lx, H - 52, lx + 18, H - 34], fill=KIND_COLOR[kind])
        label = (KIND_RU if ru else KIND_UA)[kind]
        d.text((lx + 28, H - 56), label, font=f_s, fill="#14231e")
        lx += 230
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
