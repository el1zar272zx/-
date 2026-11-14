# shared/selfcare.py — логіка розділу «Мій догляд»: челенджі, міні-курси, сезонна порада
from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy import select

from . import clientdata as cd
from . import selfcare_data as sd
from .database import session_scope
from .models import Booking, BookingStatus, Service
from .utils import now_local, today_local

logger = logging.getLogger(__name__)

# ---------- Челендж тижня ----------


def week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def challenge_of(d: date) -> dict:
    return sd.CHALLENGES[d.isocalendar()[1] % len(sd.CHALLENGES)]


async def ch_load(uid: int) -> dict:
    """Стан челенджу з автоматичним переходом на новий тиждень (серія рахується за тижні з 5+ днями)."""
    today = today_local()
    st = await cd.get_json(uid, "ch")
    if not st:
        return {}
    cur = week_key(today)
    if st.get("week") != cur:
        prev = week_key(today - timedelta(days=7))
        if st.get("week") == prev and len(st.get("days", [])) >= 5:
            st["streak"] = int(st.get("streak", 0)) + 1
            st["done"] = int(st.get("done", 0)) + 1
        else:
            st["streak"] = 0
        st["week"] = cur
        st["days"] = []
        await cd.put_json(uid, "ch", st)
    return st


def ch_bar(st: dict, today: date | None = None) -> str:
    today = today or today_local()
    monday = today - timedelta(days=today.weekday())
    days = set(st.get("days", []))
    return "".join("●" if (monday + timedelta(days=i)).isoformat() in days else "○" for i in range(7))


async def ch_join(uid: int) -> dict:
    st = {"week": week_key(today_local()), "days": [], "joined": 1, "remind": 1, "streak": 0, "done": 0}
    old = await cd.get_json(uid, "ch")
    if old:
        st["streak"] = int(old.get("streak", 0))
        st["done"] = int(old.get("done", 0))
    await cd.put_json(uid, "ch", st)
    return st


async def ch_mark(uid: int) -> tuple[dict, bool, bool]:
    """Відмітити сьогодні. Повертає (стан, чи_нова_відмітка, чи_тиждень_завершено)."""
    st = await ch_load(uid)
    if not st:
        st = await ch_join(uid)
    iso = today_local().isoformat()
    days = st.setdefault("days", [])
    new = iso not in days
    if new:
        days.append(iso)
        await cd.put_json(uid, "ch", st)
    return st, new, len(days) >= 7


# ---------- Міні-курси ----------


async def course_state(uid: int) -> dict:
    return await cd.get_json(uid, "course")


async def course_start(uid: int, slug: str) -> None:
    await cd.put_json(uid, "course", {"slug": slug, "day": 1, "last": today_local().isoformat()})


async def course_stop(uid: int) -> None:
    await cd.drop(uid, "course")


def course_day_text(slug: str, day: int, lang: str) -> str:
    c = sd.COURSES[slug]
    title = sd.pick(c["title"], lang)
    body = sd.pick(c["days"][day - 1], lang)
    head = f"<b>{title}</b>\n" + (f"День {day} з {len(c['days'])}" if lang != "ru" else f"День {day} из {len(c['days'])}")
    foot = sd.SAFE_RU if lang == "ru" else sd.SAFE_UA
    return f"{head}\n\n{body}\n\n<i>{foot}</i>"


async def course_advance(uid: int) -> tuple[str, int, bool] | None:
    """Наступний день курсу, якщо сьогодні його ще не було. Повертає (slug, день, чи_останній)."""
    st = await course_state(uid)
    slug = st.get("slug")
    if slug not in sd.COURSES:
        return None
    today = today_local()
    if st.get("last") == today.isoformat():
        return None
    day = int(st.get("day", 1)) + 1
    total = len(sd.COURSES[slug]["days"])
    if day > total:
        await course_stop(uid)
        return None
    st.update(day=day, last=today.isoformat())
    last = day >= total
    if last:
        await course_stop(uid)
    else:
        await cd.put_json(uid, "course", st)
    return slug, day, last


# ---------- Сезонна порада з урахуванням історії ----------


async def season_info(uid: int, lang: str) -> tuple[str, int | None]:
    """Текст сезонної поради та id послуги, яку клієнт брав найчастіше (для кнопки запису)."""
    today = today_local()
    text = sd.pick(sd.SEASON[today.month], lang)
    ru = lang == "ru"
    sid = None
    try:
        now = now_local().replace(tzinfo=None)
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Booking).where(
                        Booking.client_telegram_id == uid, Booking.status == BookingStatus.CONFIRMED
                    )
                )
            ).scalars().all()
            done = [b for b in rows if datetime.combine(b.date, b.end_time) <= now]
            if done:
                last = max(b.date for b in done)
                days = (today - last).days
                top_id, _ = Counter(b.service_id for b in done).most_common(1)[0]
                svc = await session.get(Service, top_id)
                extra = []
                if days >= 21:
                    extra.append(
                        f"С вашего последнего визита прошло {days} дн. Самое время вернуться к заботе о себе."
                        if ru
                        else f"З вашого останнього візиту минуло {days} дн. Саме час повернутися до турботи про себе."
                    )
                if svc is not None and svc.is_active:
                    sid = svc.id
                    extra.append(
                        f"Вы чаще всего выбираете: {svc.name}." if ru else f"Ви найчастіше обираєте: {svc.name}."
                    )
                if extra:
                    text += "\n\n" + "\n".join(extra)
    except Exception:
        logger.exception("Сезонна порада без персоналізації")
    return text, sid
