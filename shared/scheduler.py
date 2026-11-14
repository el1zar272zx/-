# shared/scheduler.py
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Awaitable, Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings
from .database import session_scope
from .backup import make_backup_copy
from .notifications_soft import (
    format_digest,
    format_review_request,
    format_admin_soon,
    format_reminder,
)
from .services import (
    get_bookings_for_date,
    get_bookings_in_period,
    get_birthday_clients,
    get_inactive_clients,
    set_client_field,
    get_bookings_for_admin_soon,
    mark_admin_notified_soon,
    count_client_visits,
    count_client_no_shows,
    get_bookings_for_notifications,
    get_bookings_needing_review,
    get_active_admin_ids,
    get_client,
    get_pending_bookings,
    list_admins,
    mark_booking_notified,
    mark_review_requested,
)
from .aftercare import (
    cleanup_old_notices,
    due_notices,
    format_free,
    format_start,
    mark_notice_sent,
    schedule_new_aftercare,
)
from .models import Service
from .reports import build_report, week_range
from .utils import esc
from .quiet import quiet_ids
from .utils import now_local, today_local

logger = logging.getLogger(__name__)


def _copy_to_extra_dir(path, name: str) -> None:
    """Додаткова копія в папку (наприклад, Google Drive / OneDrive). Зберігає останні 14."""
    import shutil
    from pathlib import Path

    from .backup import cloud_dir

    cloud = cloud_dir()
    if cloud is None:
        return
    try:
        target = cloud
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / name)
        old = sorted(target.glob("*.db"), key=lambda p: p.stat().st_mtime)[:-14]
        for p in old:
            p.unlink(missing_ok=True)
    except OSError:
        logger.exception("Не вдалося зберегти копію бази в хмарну папку")


def _local_copy(path, name: str) -> None:
    """Завжди кладе копію в папку backups поруч з ботом (останні 14)."""
    import shutil
    from pathlib import Path

    try:
        target = Path("backups")
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / name)
        for p in sorted(target.glob("*.db"), key=lambda p: p.stat().st_mtime)[:-14]:
            p.unlink(missing_ok=True)
    except OSError:
        logger.exception("Не вдалося зберегти локальну копію бази")


async def send_backup(bot: Bot, chat_ids: list[int]) -> bool:
    """Надсилає копію бази вказаним чатам. Повертає True, якщо хоч одному доставлено."""
    made = make_backup_copy()
    if made is None:
        logger.warning("Бекап неможливий: база не є файлом SQLite")
        return False
    path, name = made
    from .backup import validate_db

    good, why, _ = validate_db(path)
    if not good:
        logger.error("Свіжа копія бази не пройшла перевірку: %s", why)
        try:
            from .alerts import alert_owner

            await alert_owner(bot, "Копія бази не пройшла перевірку", why)
        except Exception:
            pass
    _copy_to_extra_dir(path, name)
    _local_copy(path, name)
    ok = False
    send_path, send_name = path, name
    try:
        import zipfile

        zpath = path.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(path, name)
        send_path, send_name = zpath, name.removesuffix(".db") + ".zip"
    except Exception:
        logger.exception("Не вдалося стиснути копію, надсилаю без стиснення")
    try:
        for chat_id in chat_ids:
            for attempt in (1, 2):
                try:
                    await bot.send_document(
                        chat_id,
                        FSInputFile(send_path, filename=send_name),
                        caption="💾 Резервна копія бази (у zip-архіві). Збережіть файл — у ньому записи та клієнти.",
                        request_timeout=90,
                    )
                    ok = True
                    break
                except TelegramAPIError:
                    logger.warning("Бекап %s: спроба %s не вдалась", chat_id, attempt)
                except Exception:
                    logger.warning("Бекап %s: спроба %s не вдалась (мережа)", chat_id, attempt)
    finally:
        try:
            send_path.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
            path.parent.rmdir()
        except OSError:
            pass
    return ok


class NotificationScheduler:
    """
    Фонові задачі: нагадування клієнтам, прохання про відгук,
    ранкова сводка та щоденний бекап для адмінів.
    """

    def __init__(
        self,
        *,
        send_message_func: Callable[[int, str], Awaitable[None]],
        client_bot: Bot | None = None,
        admin_bot: Bot | None = None,
    ) -> None:
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)
        self.send_message = send_message_func
        self.client_bot = client_bot
        self.admin_bot = admin_bot
        self._health_fails = 0

    async def _process_reviews(self) -> None:
        if self.client_bot is None:
            return
        from client_bot.keyboards import rating_kb  # уникаємо циклічних імпортів

        try:
            async with session_scope() as session:
                bookings = await get_bookings_needing_review(session)
            quiet = await quiet_ids()
            for b in bookings:
                if b.client_telegram_id <= 0:
                    async with session_scope() as session:
                        await mark_review_requested(session, b.id)
                    continue
                if b.client_telegram_id in quiet:
                    continue  # тихі години: спитаємо пізніше
                try:
                    await self.client_bot.send_message(
                        b.client_telegram_id,
                        format_review_request(b),
                        reply_markup=rating_kb(b.id),
                    )
                    async with session_scope() as session:
                        done = await count_client_visits(session, b.client_telegram_id)
                    try:
                        from . import care
                        from client_bot.keyboards import recall_kb

                        hint = await care.next_visit_hint(b)
                        if hint:
                            await self.client_bot.send_message(
                                b.client_telegram_id, hint[0], reply_markup=recall_kb(hint[1])
                            )
                    except TelegramAPIError:
                        pass
                    except Exception:
                        logger.exception("Не вдалося надіслати підказку про наступний візит")
                    if settings.loyalty_every > 0 and done and done % settings.loyalty_every == 0:
                        await self.client_bot.send_message(
                            b.client_telegram_id,
                            f"🎉 Це вже ваш {done}-й візит! Дякуємо, що ви з нами 💛",
                        )
                except TelegramForbiddenError:
                    pass  # клієнт заблокував бота — просто більше не питаємо
                except TelegramAPIError:
                    logger.exception("Не вдалося надіслати запит відгуку #%s", b.id)
                    continue
                async with session_scope() as session:
                    await mark_review_requested(session, b.id)
        except Exception:
            logger.exception("Помилка обробки запитів на відгуки")

    async def _morning_digest(self) -> None:
        if self.admin_bot is None:
            return
        try:
            day = today_local()
            async with session_scope() as session:
                bookings = await get_bookings_for_date(session, day)
                pending = await get_pending_bookings(session)
                admin_ids = await get_active_admin_ids(session)
            from . import visit_notes
            from .services import get_birthday_clients, get_day_hours

            notes_pending = len(await visit_notes.pending_notes())
            from . import jobs2

            tasks_txt = await jobs2.tasks_text()
            if not bookings and not pending and not notes_pending and not tasks_txt:
                return
            firsts: set[int] = set()
            windows: list = []
            tomorrow = 0
            birthdays: list[str] = []
            try:
                async with session_scope() as session:
                    for b in bookings:
                        if await count_client_visits(session, b.client_telegram_id) == 0:
                            firsts.add(b.id)
                    tomorrow = len(await get_bookings_for_date(session, day + timedelta(days=1)))
                    birthdays = [c.name for c in await get_birthday_clients(session, day)]
                    active = sorted(
                        (b for b in bookings if b.status.value in ("PENDING", "CONFIRMED")),
                        key=lambda x: x.start_time,
                    )
                    if active:
                        hours = await get_day_hours(session, active[0].city_id, day)
                        if hours:
                            from datetime import datetime as _dt

                            cur = _dt.combine(day, hours[0])
                            end_day = _dt.combine(day, hours[1])
                            for b in active:
                                s = _dt.combine(day, b.start_time)
                                if (s - cur).total_seconds() >= 1800:
                                    windows.append((cur.time(), s.time()))
                                cur = max(cur, _dt.combine(day, b.end_time))
                            if (end_day - cur).total_seconds() >= 1800:
                                windows.append((cur.time(), end_day.time()))
            except Exception:
                logger.exception("Не вдалося зібрати додаткові дані для сводки")
            text = format_digest(
                day,
                bookings,
                len(pending),
                firsts=firsts,
                windows=windows,
                tomorrow=tomorrow,
                birthdays=birthdays,
                notes_pending=notes_pending,
            ) + tasks_txt
            for admin_id in admin_ids:
                try:
                    await self.admin_bot.send_message(admin_id, text)
                except TelegramAPIError:
                    logger.warning("Сводку не доставлено адміну %s", admin_id)
        except Exception:
            logger.exception("Помилка ранкової сводки")

    async def _aftercare(self) -> None:
        """Після процедури: перелік обмежень, потім «вже можна» у потрібний час."""
        if self.client_bot is None:
            return
        try:
            async with session_scope() as session:
                await schedule_new_aftercare(session)
            async with session_scope() as session:
                due = await due_notices(session)
            quiet = await quiet_ids()
            for notice, booking, rule, rules in due:
                if booking is not None and booking.client_telegram_id in quiet:
                    continue
                if booking is None or booking.client_telegram_id <= 0:
                    text = None
                elif notice.kind == "start" and rules:
                    text = format_start(booking, rules)
                elif notice.kind == "free" and rule is not None:
                    text = format_free(rule)
                else:
                    text = None
                if text:
                    try:
                        await self.client_bot.send_message(booking.client_telegram_id, text)
                    except TelegramForbiddenError:
                        pass
                    except TelegramAPIError:
                        logger.exception("Рекомендації не надіслано (запис #%s)", booking.id)
                        continue
                async with session_scope() as session:
                    await mark_notice_sent(session, notice.id)
            async with session_scope() as session:
                await cleanup_old_notices(session)
        except Exception:
            logger.exception("Помилка рекомендацій після процедур")

    async def _weekly_report(self) -> None:
        if self.admin_bot is None:
            return
        try:
            first, last = week_range(today_local())
            async with session_scope() as session:
                bookings = await get_bookings_in_period(session, first, last)
                admin_ids = await get_active_admin_ids(session)
            text = build_report(bookings, first, last, "Підсумки минулого тижня")
            png = None
            try:
                import asyncio

                from .schedule_image import render_week

                today = today_local()
                async with session_scope() as session:
                    ahead = await get_bookings_in_period(session, today, today + timedelta(days=6))
                png = await asyncio.to_thread(render_week, ahead, today)
            except Exception:
                logger.exception("Не вдалося намалювати розклад тижня")
            from aiogram.types import BufferedInputFile

            for admin_id in admin_ids:
                try:
                    await self.admin_bot.send_message(admin_id, text)
                    if png:
                        await self.admin_bot.send_photo(
                            admin_id,
                            BufferedInputFile(png, filename="rozklad.png"),
                            caption="🗓 Розклад на цей тиждень",
                        )
                except TelegramAPIError:
                    logger.warning("Звіт не доставлено адміну %s", admin_id)
        except Exception:
            logger.exception("Помилка тижневого звіту")

    async def _reengage(self) -> None:
        """Лагідне нагадування клієнтам, які не були давно."""
        if self.client_bot is None:
            return
        from client_bot.keyboards import repeat_kb

        try:
            async with session_scope() as session:
                clients = await get_inactive_clients(session, settings.reengage_days)
            quiet = await quiet_ids()
            for c in clients:
                if c.telegram_id in quiet:
                    continue
                try:
                    await self.client_bot.send_message(
                        c.telegram_id,
                        f"Доброго дня, {c.name}! 🌿 Давно не бачились. "
                        "Якщо хочете підтримати себе масажем, оберіть зручний час у меню «📝 Записатися».",
                        reply_markup=repeat_kb(),
                    )
                except TelegramAPIError:
                    pass  # заблокував бота або чат недоступний — просто не повторюємо
                async with session_scope() as session:
                    await set_client_field(
                        session, c.telegram_id, last_reengage_on=today_local()
                    )
        except Exception:
            logger.exception("Помилка нагадування давнім клієнтам")

    async def _recall(self) -> None:
        """Нагадування «пора на повторний сеанс» з урахуванням виду процедури."""
        if self.client_bot is None:
            return
        from . import care
        from client_bot.keyboards import recall_kb

        try:
            if not await care.get_flag("recall_on"):
                return
            quiet = await quiet_ids()
            for b, svc, days in await care.due_recalls():
                if b.client_telegram_id in quiet:
                    continue
                try:
                    await self.client_bot.send_message(
                        b.client_telegram_id,
                        f"Доброго дня, {b.client_name}! 🌿 Минуло трохи часу після процедури "
                        f"«{svc.name}». Зазвичай її повторюють приблизно раз на "
                        f"{care.interval_label(days)}. Якщо вам зручно, оберіть час.",
                        reply_markup=recall_kb(svc.id),
                    )
                except TelegramAPIError:
                    pass
                await care.mark_recall_sent(b.id)
        except Exception:
            logger.exception("Помилка нагадувань про повторний візит")

    async def _followup(self) -> None:
        """Через кілька днів після процедури питаємо про самопочуття (лише вдень)."""
        if self.client_bot is None or not 10 <= now_local().hour < 20:
            return
        from . import care
        from client_bot.keyboards import followup_kb

        try:
            if not await care.get_flag("followup_on"):
                return
            quiet = await quiet_ids()
            for b, svc in await care.due_followups():
                if b.client_telegram_id in quiet:
                    continue
                try:
                    await self.client_bot.send_message(
                        b.client_telegram_id,
                        f"Добрий день, {b.client_name}! Як ви почуваєтесь після процедури "
                        f"«{svc.name}»? Якщо щось турбує, натисніть кнопку, і майстер вам напише.",
                        reply_markup=followup_kb(b.id),
                    )
                except TelegramAPIError:
                    pass
                await care.mark_followup_sent(b.id)
        except Exception:
            logger.exception("Помилка опитування про самопочуття")

    async def _notes_evening(self) -> None:
        """Увечері нагадує про візити без нотаток. Нічого не вимагає: можна дописати пізніше."""
        if self.admin_bot is None:
            return
        try:
            from . import visit_notes

            items = [b for b in await visit_notes.pending_notes() if b.date == today_local()]
            if not items:
                return
            async with session_scope() as session:
                admin_ids = await get_active_admin_ids(session)
            from admin_bot.keyboards import btn, kb

            text = (
                f"📝 Сьогодні візитів без нотаток: {len(items)}. "
                "Якщо зараз немає сил, нічого страшного: список лишається 2 тижні."
            )
            markup = kb([[btn("📝 Записати нотатки", "vn_list")]])
            for admin_id in admin_ids:
                try:
                    await self.admin_bot.send_message(admin_id, text, reply_markup=markup)
                except TelegramAPIError:
                    pass
        except Exception:
            logger.exception("Помилка нагадування про нотатки")

    async def _scheduled_broadcasts(self) -> None:
        """Відправляє відкладені розсилки, коли настав їхній час."""
        if self.client_bot is None:
            return
        try:
            from . import broadcast as bc

            for item in await bc.due():
                await bc.mark_done(item.id)  # спершу позначаємо, щоб не відправити двічі
                ids = await bc.segment_ids(item.segment)
                ok, fail, skipped = await bc.send_many(self.client_bot, ids, item.text)
                if self.admin_bot is not None:
                    async with session_scope() as session:
                        admin_ids = await get_active_admin_ids(session)
                    note = f" Не турбувала (тихі години): {skipped}." if skipped else ""
                    for admin_id in admin_ids:
                        try:
                            await self.admin_bot.send_message(
                                admin_id,
                                f"📣 Запланована розсилка надіслана: доставлено {ok}, не доставлено {fail}.{note}",
                            )
                        except TelegramAPIError:
                            pass
        except Exception:
            logger.exception("Помилка відкладених розсилок")

    async def _holidays(self) -> None:
        """Пропонує майстру закрити державне свято. Нічого не змінює без її згоди."""
        if self.admin_bot is None:
            return
        try:
            from . import holidays
            from .services import get_active_cities, get_day_hours

            if not await holidays.enabled():
                return
            today = today_local()
            for day, name in holidays.holidays_between(
                today + timedelta(days=1), today + timedelta(days=21)
            ):
                if await holidays.decision(day):
                    continue
                async with session_scope() as session:
                    cities = await get_active_cities(session)
                    if not cities:
                        return
                    hours = await get_day_hours(session, cities[0].id, day)
                    admin_ids = await get_active_admin_ids(session)
                if hours is None:
                    await holidays.set_decision(day, "off")  # і так вихідний
                    continue
                from admin_bot.keyboards import btn, kb

                text = (
                    f"🎉 <b>{esc(name)}</b> · {day:%d.%m.%Y}\n"
                    "Цього дня у вас зараз робочий графік. Закрити запис на цей день?\n"
                    "Це лише підказка: рішення за вами."
                )
                markup = kb(
                    [
                        [btn("🔒 Закрити день", f"hol_close_{day.isoformat()}")],
                        [btn("✅ Лишити робочим", f"hol_keep_{day.isoformat()}")],
                    ]
                )
                for admin_id in admin_ids:
                    try:
                        await self.admin_bot.send_message(admin_id, text, reply_markup=markup)
                    except TelegramAPIError:
                        logger.warning("Свято: не доставлено адміну %s", admin_id)
                await holidays.set_decision(day, "asked")
        except Exception:
            logger.exception("Помилка підказок про свята")

    async def _health(self) -> None:
        """Раз на 10 хвилин: база й Telegram відповідають? Збій двічі підряд — сповіщення власнику."""
        from sqlalchemy import text

        from .alerts import alert_owner
        from .health import touch_heartbeat

        problems: list[str] = []
        try:
            async with session_scope() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            problems.append(f"база даних: {type(exc).__name__}")
        try:
            if self.client_bot is not None:
                await self.client_bot.get_me()
        except Exception as exc:
            problems.append(f"Telegram (клієнтський бот): {type(exc).__name__}")
        if problems:
            self._health_fails += 1
            if self._health_fails == 2:
                await alert_owner(self.admin_bot, "Бот працює з перебоями", "; ".join(problems))
        else:
            if self._health_fails >= 2:
                await alert_owner(self.admin_bot, "Роботу бота відновлено")
            self._health_fails = 0
            touch_heartbeat()

    async def _birthdays(self) -> None:
        if self.client_bot is None:
            return
        try:
            today = today_local()
            async with session_scope() as session:
                clients = await get_birthday_clients(session, today)
            quiet = await quiet_ids()
            for c in clients:
                if c.telegram_id in quiet:
                    continue
                try:
                    await self.client_bot.send_message(
                        c.telegram_id,
                        f"🎂 {c.name}, вітаємо вас з днем народження! "
                        "Здоровʼя, спокою й легкості в тілі 💛",
                    )
                except TelegramAPIError:
                    pass
                async with session_scope() as session:
                    await set_client_field(
                        session, c.telegram_id, birthday_greeted_year=today.year
                    )
        except Exception:
            logger.exception("Помилка вітань з днем народження")

    async def _daily_backup(self) -> None:
        if self.admin_bot is None:
            return
        try:
            async with session_scope() as session:
                admins = await list_admins(session)
            ids = [a.telegram_id for a in admins if a.is_superadmin]
            from .backup import live_integrity

            healthy, why = live_integrity()
            if not healthy:
                from .alerts import alert_owner

                await alert_owner(self.admin_bot, "Робоча база пошкоджена", why)
            if ids:
                await send_backup(self.admin_bot, ids)
        except Exception:
            logger.exception("Помилка щоденного бекапу")

    async def _admin_soon(self) -> None:
        if self.admin_bot is None:
            return
        try:
            async with session_scope() as session:
                bookings = await get_bookings_for_admin_soon(session)
                admin_ids = await get_active_admin_ids(session)
                visits = {
                    b.id: await count_client_visits(session, b.client_telegram_id)
                    for b in bookings
                }
                no_shows = {
                    b.id: await count_client_no_shows(session, b.client_telegram_id)
                    for b in bookings
                }
                call_ok = {}
                for b in bookings:
                    c = await get_client(session, b.client_telegram_id)
                    call_ok[b.id] = bool(c and c.contact_pref == "call")
            for b in bookings:
                text = format_admin_soon(
                    b,
                    settings.admin_notify_minutes_before,
                    visits.get(b.id),
                    no_shows.get(b.id),
                    call_ok.get(b.id, False),
                )
                for admin_id in admin_ids:
                    try:
                        await self.admin_bot.send_message(admin_id, text)
                    except TelegramAPIError:
                        logger.warning("Не доставлено адміну %s (запис #%s)", admin_id, b.id)
                async with session_scope() as session:
                    await mark_admin_notified_soon(session, b.id)
        except Exception:
            logger.exception("Помилка сповіщення адміна про візит")

    async def _process_notifications(self, hours_before: int) -> None:
        try:
            async with session_scope() as session:
                bookings = await get_bookings_for_notifications(
                    session, hours_before=hours_before
                )

            quiet = await quiet_ids() if hours_before >= 6 else set()
            for booking in bookings:
                if booking.client_telegram_id in quiet:
                    continue  # тихі години: нагадаємо пізніше
                if booking.client_telegram_id <= 0:
                    # клієнт записаний по телефону, без Telegram: нагадувати нема кому
                    async with session_scope() as session:
                        await mark_booking_notified(
                            session, booking.id, hours_before=hours_before
                        )
                    continue
                text = format_reminder(booking, hours_before)
                if hours_before == settings.notify_hours_before_1:
                    async with session_scope() as session:
                        svc = await session.get(Service, booking.service_id)
                    if svc is not None and svc.prep:
                        text += f"\n\n🧴 <b>Як підготуватися:</b> {esc(svc.prep)}"
                try:
                    if self.client_bot is not None:
                        from client_bot.keyboards import reminder_kb

                        await self.client_bot.send_message(
                            booking.client_telegram_id,
                            text,
                            reply_markup=(
                                reminder_kb(booking.id) if hours_before >= 6 else None
                            ),
                        )
                    else:
                        await self.send_message(booking.client_telegram_id, text)
                except TelegramForbiddenError:
                    # Клієнт заблокував бота: позначаємо, щоб не повторювати щоп'ять хвилин
                    logger.warning(
                        "Клієнт %s заблокував бота, нагадування для запису #%s пропущено",
                        booking.client_telegram_id,
                        booking.id,
                    )
                except Exception:
                    logger.exception(
                        "Не вдалося надіслати нагадування для запису #%s", booking.id
                    )
                    continue
                async with session_scope() as session:
                    await mark_booking_notified(
                        session, booking.id, hours_before=hours_before
                    )
        except Exception:
            logger.exception("Помилка обробки нагадувань (%s год.)", hours_before)

    def start(self) -> None:
        """Викликати всередині запущеного event loop (з async-функції)."""
        if self.scheduler.running:
            return
        self.scheduler.add_job(
            self._admin_soon,
            trigger=IntervalTrigger(minutes=5),
            id="admin_soon",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        for job_id, hours in (
            ("notify_first", settings.notify_hours_before_1),
            ("notify_mid", settings.notify_hours_before_mid),
            ("notify_second", settings.notify_hours_before_2),
        ):
            self.scheduler.add_job(
                self._process_notifications,
                trigger=IntervalTrigger(minutes=5),
                args=[hours],
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        self.scheduler.add_job(
            self._process_reviews,
            trigger=IntervalTrigger(minutes=10),
            id="reviews",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.add_job(
            self._morning_digest,
            trigger=CronTrigger(hour=settings.morning_digest_hour, minute=0),
            id="morning_digest",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._aftercare,
            trigger=IntervalTrigger(minutes=10),
            id="aftercare", replace_existing=True, max_instances=1, coalesce=True,
        )
        self.scheduler.add_job(
            self._weekly_report,
            trigger=CronTrigger(day_of_week="mon", hour=9, minute=0),
            id="weekly_report", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._reengage,
            trigger=CronTrigger(hour=12, minute=0),
            id="reengage", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._birthdays,
            trigger=CronTrigger(hour=10, minute=0),
            id="birthdays", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._daily_backup,
            trigger=CronTrigger(hour=settings.backup_hour, minute=0),
            id="daily_backup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._recall,
            trigger=CronTrigger(hour=11, minute=0),
            id="recall", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._followup,
            trigger=IntervalTrigger(minutes=30),
            id="followup", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=600,
        )
        self.scheduler.add_job(
            self._notes_evening,
            trigger=CronTrigger(hour=20, minute=30),
            id="notes_evening", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._scheduled_broadcasts,
            trigger=IntervalTrigger(minutes=1),
            id="scheduled_broadcasts", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=120,
        )
        self.scheduler.add_job(
            self._holidays,
            trigger=CronTrigger(hour=10, minute=20),
            id="holidays", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=3600,
        )
        self.scheduler.add_job(
            self._health,
            trigger=IntervalTrigger(minutes=10),
            id="health", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=300,
        )
        from . import jobs2, jobs3

        for job_id, func, args, trig in (
            ("day_summary", jobs2.day_summary, [self.admin_bot], CronTrigger(hour=21, minute=0)),
            ("achievements", jobs2.achievements, [self.admin_bot], IntervalTrigger(hours=1)),
            ("empty_hours", jobs2.empty_hours, [self.admin_bot], CronTrigger(day_of_week="mon", hour=10, minute=30)),
            ("weekly_tip", jobs2.weekly_tip, [self.client_bot], CronTrigger(day_of_week="thu", hour=11, minute=30)),
            ("drafts_nudge", jobs2.drafts_nudge, [self.client_bot], IntervalTrigger(minutes=30)),
            ("selfcare_daily", jobs3.selfcare_daily, [self.client_bot], CronTrigger(hour=18, minute=30)),
            ("selfcare_morning", jobs3.selfcare_morning, [self.client_bot], CronTrigger(hour=10, minute=15)),
            ("year_cards", jobs3.year_cards, [self.client_bot], CronTrigger(month=12, day=27, hour=12, minute=0)),
        ):
            self.scheduler.add_job(
                func, args=args, trigger=trig, id=job_id, replace_existing=True,
                max_instances=1, coalesce=True, misfire_grace_time=1800,
            )
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
