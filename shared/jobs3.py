# shared/jobs3.py — фонові завдання розділу «Мій догляд»: нагадування щоденника, челенджу, фото шкіри,
# міні-курси, анкета комфорту після першого візиту, «Ваш рік з нами»
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from . import clientdata as cd
from . import selfcare, wellbeing, yearcard
from . import selfcare_data as sd
from .database import session_scope
from .i18n import _lang_of
from .models import Booking, BookingStatus, Client, SalonInfo
from .quiet import quiet_ids
from .utils import now_local, today_local

logger = logging.getLogger(__name__)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def score_rows(prefix: str) -> list[list[InlineKeyboardButton]]:
    return [
        [_btn(str(n), f"{prefix}{n}") for n in range(1, 6)],
        [_btn(str(n), f"{prefix}{n}") for n in range(6, 11)],
    ]


async def _send(bot: Bot, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None) -> bool:
    for _ in (1, 2):
        try:
            await bot.send_message(chat_id, text, reply_markup=markup)
            await asyncio.sleep(0.06)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramAPIError, OSError, asyncio.TimeoutError):
            return False
    return False


async def _bookings_on(day, tids: set[int]) -> list[Booking]:
    if not tids:
        return []
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Booking).where(
                    Booking.date == day,
                    Booking.client_telegram_id.in_(list(tids)),
                    Booking.status.in_((BookingStatus.PENDING, BookingStatus.CONFIRMED)),
                )
            )
        ).scalars().all()
    return list(rows)


async def selfcare_daily(client_bot: Bot | None) -> None:
    """Вечір (18:30): оцінка «до візиту», челендж, фото шкіри."""
    if client_bot is None:
        return
    try:
        quiet = await quiet_ids()
        today = today_local()

        # 1) оцінка самопочуття напередодні візиту
        on = {t for t, v in await cd.rows_for_key("wb_on") if v == "1" and t > 0}
        for b in await _bookings_on(today + timedelta(days=1), on):
            if b.client_telegram_id in quiet or await wellbeing.has_entry(b.client_telegram_id, b.id, "before"):
                continue
            await _send(
                client_bot,
                b.client_telegram_id,
                f"💗 Завтра ваш візит ({b.title}, {b.start_time:%H:%M}). Як ваше тіло зараз? "
                "Оцініть біль чи напругу від 1 до 10: так ми побачимо, чи допомагає процедура.",
                _kb(score_rows(f"wb_b_{b.id}_")),
            )

        # 2) челендж тижня
        ch = selfcare.challenge_of(today)
        for tid, raw in await cd.rows_for_key("ch"):
            if tid <= 0 or tid in quiet:
                continue
            try:
                st = json.loads(raw)
            except ValueError:
                continue
            if not st.get("joined") or not st.get("remind", 1):
                continue
            if st.get("week") == selfcare.week_key(today) and today.isoformat() in st.get("days", []):
                continue
            lang = await _lang_of(tid)
            await _send(
                client_bot,
                tid,
                f"🏆 {sd.pick(ch['title'], lang)}. Сьогодні вже встигли? Відмітьте одним дотиком 🌿",
                _kb([[_btn("✅ Сьогодні зроблено", "ch_done")], [_btn("🏆 Челендж", "ch_menu")]]),
            )

        # 3) фото шкіри раз на 2 тижні
        for tid, flag in await cd.rows_for_key("ph_on"):
            if flag != "1" or tid <= 0 or tid in quiet:
                continue
            last = await cd.get(tid, "ph_rem")
            try:
                last_d = datetime.fromisoformat(last).date() if last else None
            except ValueError:
                last_d = None
            if last_d is not None and (today - last_d).days < 14:
                continue
            await cd.put(tid, "ph_rem", today.isoformat())
            await _send(
                client_bot,
                tid,
                "📸 Час зробити фото шкіри для вашого щоденника (раз на 2 тижні). Денне світло, без макіяжу.",
                _kb([[_btn("📸 Додати фото", "ph_add")]]),
            )
    except Exception:
        logger.exception("Помилка вечірніх нагадувань «Мій догляд»")


async def selfcare_morning(client_bot: Bot | None) -> None:
    """Ранок (10:15): оцінка «після візиту», день курсу, анкета комфорту після першого візиту."""
    if client_bot is None:
        return
    try:
        quiet = await quiet_ids()
        today = today_local()
        yesterday = today - timedelta(days=1)

        # 1) оцінка після візиту
        on = {t for t, v in await cd.rows_for_key("wb_on") if v == "1" and t > 0}
        for b in await _bookings_on(yesterday, on):
            if b.status != BookingStatus.CONFIRMED or b.client_telegram_id in quiet:
                continue
            if await wellbeing.has_entry(b.client_telegram_id, b.id, "after"):
                continue
            await _send(
                client_bot,
                b.client_telegram_id,
                "💗 Вчора у вас був візит. Як почувається тіло? Оцініть біль чи напругу від 1 до 10.",
                _kb(score_rows(f"wb_a_{b.id}_")),
            )

        # 2) міні-курси: наступний день
        for tid, _raw in await cd.rows_for_key("course"):
            if tid <= 0 or tid in quiet:
                continue
            nxt = await selfcare.course_advance(tid)
            if nxt is None:
                continue
            slug, day, last = nxt
            lang = await _lang_of(tid)
            tail = "\n\n🎉 Курс завершено! Повторюйте комплекс через день." if last else ""
            await _send(
                client_bot,
                tid,
                "🌿 Доброго ранку! Ваше завдання на сьогодні:\n\n" + selfcare.course_day_text(slug, day, lang) + tail,
                _kb([[_btn("📅 Міні-курси", "cr_menu")]]),
            )

        # 3) анкета комфорту після першого візиту
        candidates: list[int] = []
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Booking).where(Booking.date == yesterday, Booking.status == BookingStatus.CONFIRMED)
                )
            ).scalars().all()
            for tid in {b.client_telegram_id for b in rows}:
                if tid <= 0 or tid in quiet:
                    continue
                total = await session.scalar(
                    select(func.count(Booking.id)).where(
                        Booking.client_telegram_id == tid,
                        Booking.status == BookingStatus.CONFIRMED,
                        Booking.date <= yesterday,
                    )
                )
                if total == 1:
                    candidates.append(tid)
        for tid in candidates:
            if await cd.get(tid, "comfort_asked") or await cd.get(tid, "comfort"):
                continue
            await cd.put(tid, "comfort_asked", "1")
            await _send(
                client_bot,
                tid,
                "🎛 Дякуємо за перший візит! Допоможіть підібрати комфортний формат сеансу: 4 швидкі запитання. "
                "Майстер врахує відповіді наступного разу.",
                _kb([[_btn("✏️ Відповісти", "cf_q_0")]]),
            )
    except Exception:
        logger.exception("Помилка ранкових повідомлень «Мій догляд»")


async def year_cards(client_bot: Bot | None) -> None:
    """Раз на рік (27 грудня): персональна картка «Ваш рік з нами»."""
    if client_bot is None:
        return
    year = today_local().year
    key = f"year_sent_{year}"
    try:
        async with session_scope() as session:
            if await session.get(SalonInfo, key) is not None:
                return
            session.add(SalonInfo(key=key, value="1"))
        from .brand import brand as brand_name

        brand = await brand_name()
        quiet = await quiet_ids()
        async with session_scope() as session:
            clients = (await session.execute(select(Client).where(Client.telegram_id > 0))).scalars().all()
        sent = 0
        for c in clients:
            if c.telegram_id in quiet:
                continue
            st = await yearcard.stats(c.telegram_id, year)
            if st is None:
                continue
            lang = c.lang or "uk"
            name = (c.name or "").split()[0] if c.name else ""
            try:
                data = yearcard.render(st, name, brand, lang)
                await client_bot.send_photo(
                    c.telegram_id,
                    BufferedInputFile(data, "year.png"),
                    caption=yearcard.caption(st, name, lang),
                )
                sent += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (TelegramAPIError, OSError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.1)
        logger.info("Надіслано карток «Ваш рік з нами»: %s", sent)
    except Exception:
        logger.exception("Помилка розсилки «Ваш рік з нами»")
