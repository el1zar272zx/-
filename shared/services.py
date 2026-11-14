# shared/services.py
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import session_scope
from .models import (
    AdminUser,
    BlockedSlot,
    Booking,
    BookingStatus,
    CardSettings,
    City,
    Client,
    Review,
    WaitlistEntry,
    PaymentMethod,
    Schedule,
    ScheduleException,
    Service,
    ServiceCategory,
)
from .utils import minutes, minutes_to_time, now_local, today_local

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)
NOTIFY_WINDOW_MINUTES = 35  # опитуємо кожні 5 хв, вікно з запасом


class BookingError(ValueError):
    """Помилка створення запису з текстом, який можна показати клієнту."""


# Один процес — один замок: SQLite не вміє блокувати рядки (SELECT ... FOR UPDATE),
# тому перевірка перетину + вставка виконуються під asyncio-замком.
_booking_lock = asyncio.Lock()


# ================== Довідники ==================


async def get_active_cities(session: AsyncSession) -> list[City]:
    res = await session.execute(
        select(City).where(City.is_active.is_(True)).order_by(City.id)
    )
    return list(res.scalars().all())


async def get_all_cities(session: AsyncSession) -> list[City]:
    res = await session.execute(select(City).order_by(City.id))
    return list(res.scalars().all())


async def get_city(session: AsyncSession, city_id: int) -> City | None:
    return await session.get(City, city_id)


# ---------- Клієнти ----------


async def get_client(session: AsyncSession, telegram_id: int) -> Client | None:
    res = await session.execute(select(Client).where(Client.telegram_id == telegram_id))
    return res.scalar_one_or_none()


async def save_client(
    session: AsyncSession, telegram_id: int, name: str, phone: str
) -> Client:
    """Створює клієнта або оновлює його імʼя/номер."""
    client = await get_client(session, telegram_id)
    if client is None:
        client = Client(telegram_id=telegram_id, name=name, phone=phone)
        session.add(client)
    else:
        client.name = name
        client.phone = phone
    await session.flush()
    return client


async def get_categories(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[ServiceCategory]:
    q = select(ServiceCategory).order_by(ServiceCategory.id)
    if not include_inactive:
        q = q.where(ServiceCategory.is_active.is_(True))
    return list((await session.execute(q)).scalars().all())


async def get_category(session: AsyncSession, category_id: int) -> ServiceCategory | None:
    return await session.get(ServiceCategory, category_id)


async def get_services_by_category(
    session: AsyncSession, category_id: int, *, include_inactive: bool = False
) -> list[Service]:
    q = select(Service).where(Service.category_id == category_id).order_by(Service.id)
    if not include_inactive:
        q = q.where(Service.is_active.is_(True))
    return list((await session.execute(q)).scalars().all())


async def get_service(session: AsyncSession, service_id: int) -> Service | None:
    return await session.get(Service, service_id)


# ================== Керування послугами (адмінка) ==================


async def create_category(session: AsyncSession, name: str) -> ServiceCategory:
    existing = (
        await session.execute(select(ServiceCategory).where(ServiceCategory.name == name))
    ).scalars().first()
    if existing:
        raise ValueError("Категорія з такою назвою вже існує.")
    cat = ServiceCategory(name=name, prices_editable_in_admin=True)
    session.add(cat)
    await session.flush()
    return cat


async def create_service(
    session: AsyncSession,
    *,
    category_id: int,
    name: str,
    duration_minutes: int,
    price: int | None,
) -> Service:
    existing = (
        await session.execute(
            select(Service).where(
                Service.category_id == category_id, Service.name == name
            )
        )
    ).scalars().first()
    if existing:
        raise ValueError("Послуга з такою назвою в цій категорії вже є.")
    svc = Service(
        category_id=category_id,
        name=name,
        duration_minutes=duration_minutes,
        price=price,
    )
    session.add(svc)
    await session.flush()
    return svc


async def update_service(
    session: AsyncSession,
    service_id: int,
    *,
    name: str | None = None,
    duration_minutes: int | None = None,
    price: int | None = None,
    set_price: bool = False,
    is_active: bool | None = None,
    description: str | None = None,
    set_description: bool = False,
) -> Service | None:
    """price записується лише коли set_price=True (щоб можна було скинути в None)."""
    svc = await session.get(Service, service_id)
    if not svc:
        return None
    if name is not None:
        svc.name = name
    if duration_minutes is not None:
        svc.duration_minutes = duration_minutes
    if set_price:
        svc.price = price
    if set_description:
        svc.description = description
    if is_active is not None:
        svc.is_active = is_active
    await session.flush()
    return svc


# ================== Розклад ==================


def _hours_from(
    weekly: Schedule | None, exc: ScheduleException | None
) -> tuple[time, time] | None:
    if exc is not None:
        if exc.is_day_off or exc.start_time is None or exc.end_time is None:
            return None
        return exc.start_time, exc.end_time
    if weekly is None or weekly.is_day_off:
        return None
    return weekly.start_time, weekly.end_time


async def get_day_hours(
    session: AsyncSession, city_id: int, day: date
) -> tuple[time, time] | None:
    """Робочі години міста на дату (виняток має пріоритет над тижневим шаблоном)."""
    exc = (
        await session.execute(
            select(ScheduleException).where(
                ScheduleException.city_id == city_id, ScheduleException.date == day
            )
        )
    ).scalars().first()
    weekly = (
        await session.execute(
            select(Schedule).where(
                Schedule.city_id == city_id, Schedule.weekday == day.weekday()
            )
        )
    ).scalars().first()
    return _hours_from(weekly, exc)


async def get_working_dates(
    session: AsyncSession,
    city_id: int,
    *,
    start: date | None = None,
    days: int | None = None,
) -> list[date]:
    """Дати, коли місто працює (у межах max_days_ahead). Вихідні не повертаються."""
    start = start or today_local()
    days = settings.max_days_ahead if days is None else days
    end = start + timedelta(days=days)

    weekly = {
        s.weekday: s
        for s in (
            await session.execute(select(Schedule).where(Schedule.city_id == city_id))
        ).scalars()
    }
    excs = {
        e.date: e
        for e in (
            await session.execute(
                select(ScheduleException).where(
                    ScheduleException.city_id == city_id,
                    ScheduleException.date >= start,
                    ScheduleException.date <= end,
                )
            )
        ).scalars()
    }

    now = now_local()
    lead = timedelta(minutes=settings.min_lead_minutes)
    result: list[date] = []
    for i in range(days + 1):
        d = start + timedelta(days=i)
        hours = _hours_from(weekly.get(d.weekday()), excs.get(d))
        if hours is None:
            continue
        if d == now.date() and datetime.combine(d, hours[1]) <= now + lead:
            continue  # на сьогодні вже запізно
        result.append(d)
    return result


async def _busy_intervals(
    session: AsyncSession,
    day: date,
    city_id: int,
    exclude_booking_id: int | None = None,
) -> list[tuple[int, int]]:
    """Зайняті інтервали в хвилинах від 00:00: записи + заблокований час."""
    q = select(Booking.start_time, Booking.end_time).where(
        Booking.date == day, Booking.status.in_(ACTIVE_STATUSES)
    )
    if exclude_booking_id is not None:
        q = q.where(Booking.id != exclude_booking_id)
    if not settings.single_specialist:
        q = q.where(Booking.city_id == city_id)
    buf = max(0, settings.buffer_minutes)
    rows = [
        (a, b)  # заповнюється нижче з паузою
        for a, b in (await session.execute(q)).all()
    ]
    rows = [
        (minutes_to_time(max(0, minutes(a) - buf)), minutes_to_time(min(24 * 60 - 1, minutes(b) + buf)))
        for a, b in rows
    ]
    blocked = (
        await session.execute(
            select(BlockedSlot.start_time, BlockedSlot.end_time).where(
                BlockedSlot.date == day
            )
        )
    ).all()
    return [(minutes(a), minutes(b)) for a, b in [*rows, *blocked]]


def _is_free(start_min: int, duration: int, busy: list[tuple[int, int]]) -> bool:
    end_min = start_min + duration
    return all(not (start_min < b_end and end_min > b_start) for b_start, b_end in busy)


def _earliest_minute_today(today: date) -> int:
    n = now_local() + timedelta(minutes=settings.min_lead_minutes)
    if n.date() > today:
        return 24 * 60
    return n.hour * 60 + n.minute + (1 if n.second else 0)


async def get_available_time_slots(
    session: AsyncSession,
    *,
    city_id: int,
    for_date: date,
    service: Service | None = None,
    duration_minutes: int | None = None,
    exclude_booking_id: int | None = None,
) -> list[time]:
    """
    Вільні слоти на дату з урахуванням: графіка міста (з винятками), записів,
    заблокованого часу, кроку слотів та того, що час на сьогодні вже міг минути.
    """
    today = today_local()
    if for_date < today or for_date > today + timedelta(days=settings.max_days_ahead):
        return []
    hours = await get_day_hours(session, city_id, for_date)
    if hours is None:
        return []

    work_start, work_end = minutes(hours[0]), minutes(hours[1])
    busy = await _busy_intervals(session, for_date, city_id, exclude_booking_id)
    earliest = _earliest_minute_today(today) if for_date == today else 0
    duration = duration_minutes if duration_minutes is not None else service.duration_minutes
    step = max(5, settings.slot_step_minutes)

    slots: list[time] = []
    cur = work_start
    while cur + duration <= work_end:
        if cur >= earliest and _is_free(cur, duration, busy):
            slots.append(minutes_to_time(cur))
        cur += step
    return slots


# ================== Керування графіком (адмінка) ==================


async def get_weekly_schedule(
    session: AsyncSession, city_id: int
) -> dict[int, Schedule]:
    res = await session.execute(select(Schedule).where(Schedule.city_id == city_id))
    return {s.weekday: s for s in res.scalars()}


async def set_weekly_day(
    session: AsyncSession,
    city_id: int,
    weekday: int,
    *,
    start: time | None = None,
    end: time | None = None,
    is_day_off: bool | None = None,
) -> Schedule:
    row = (
        await session.execute(
            select(Schedule).where(
                Schedule.city_id == city_id, Schedule.weekday == weekday
            )
        )
    ).scalars().first()
    if row is None:
        row = Schedule(
            city_id=city_id,
            weekday=weekday,
            start_time=start or time(10, 0),
            end_time=end or time(20, 0),
            is_day_off=bool(is_day_off),
        )
        session.add(row)
    else:
        if start is not None and end is not None:
            if start >= end:
                raise ValueError("Початок має бути раніше за кінець.")
            row.start_time, row.end_time = start, end
        if is_day_off is not None:
            row.is_day_off = is_day_off
    await session.flush()
    return row


async def get_exception(
    session: AsyncSession, city_id: int, day: date
) -> ScheduleException | None:
    return (
        await session.execute(
            select(ScheduleException).where(
                ScheduleException.city_id == city_id, ScheduleException.date == day
            )
        )
    ).scalars().first()


async def upsert_exception(
    session: AsyncSession,
    city_id: int,
    day: date,
    *,
    is_day_off: bool,
    start: time | None = None,
    end: time | None = None,
) -> ScheduleException:
    if not is_day_off and (start is None or end is None or start >= end):
        raise ValueError("Вкажіть коректні години роботи.")
    exc = await get_exception(session, city_id, day)
    if exc is None:
        exc = ScheduleException(city_id=city_id, date=day)
        session.add(exc)
    exc.is_day_off = is_day_off
    exc.start_time = None if is_day_off else start
    exc.end_time = None if is_day_off else end
    await session.flush()
    return exc


async def delete_exception(session: AsyncSession, city_id: int, day: date) -> bool:
    exc = await get_exception(session, city_id, day)
    if exc is None:
        return False
    await session.delete(exc)
    await session.flush()
    return True


async def list_exceptions(
    session: AsyncSession, city_id: int, from_day: date | None = None
) -> list[ScheduleException]:
    res = await session.execute(
        select(ScheduleException)
        .where(
            ScheduleException.city_id == city_id,
            ScheduleException.date >= (from_day or today_local()),
        )
        .order_by(ScheduleException.date)
    )
    return list(res.scalars().all())


async def add_blocked_slot(
    session: AsyncSession,
    day: date,
    start: time,
    end: time,
    note: str | None = None,
) -> BlockedSlot:
    if start >= end:
        raise ValueError("Початок має бути раніше за кінець.")
    slot = BlockedSlot(date=day, start_time=start, end_time=end, note=note or None)
    session.add(slot)
    await session.flush()
    return slot


async def list_blocked_slots(
    session: AsyncSession, from_day: date | None = None
) -> list[BlockedSlot]:
    res = await session.execute(
        select(BlockedSlot)
        .where(BlockedSlot.date >= (from_day or today_local()))
        .order_by(BlockedSlot.date, BlockedSlot.start_time)
    )
    return list(res.scalars().all())


async def delete_blocked_slot(session: AsyncSession, slot_id: int) -> bool:
    slot = await session.get(BlockedSlot, slot_id)
    if slot is None:
        return False
    await session.delete(slot)
    await session.flush()
    return True


# ================== Бронювання ==================


async def count_client_visits(session: AsyncSession, telegram_id: int) -> int:
    """Скільки підтверджених візитів клієнта вже відбулося."""
    now = now_local()
    res = await session.execute(
        select(func.count(Booking.id)).where(
            Booking.client_telegram_id == telegram_id,
            Booking.status == BookingStatus.CONFIRMED,
            or_(
                Booking.date < now.date(),
                and_(Booking.date == now.date(), Booking.end_time <= now.time()),
            ),
        )
    )
    return int(res.scalar() or 0)


async def count_client_no_shows(session: AsyncSession, telegram_id: int) -> int:
    res = await session.execute(
        select(func.count(Booking.id)).where(
            Booking.client_telegram_id == telegram_id,
            Booking.status == BookingStatus.NO_SHOW,
        )
    )
    return int(res.scalar() or 0)


def phone_pseudo_id(phone: str) -> int:
    """Стабільний від'ємний «ID» для клієнта без Telegram (запис по телефону)."""
    digits = "".join(ch for ch in phone if ch.isdigit()) or "0"
    return -int(digits)


def loyalty_applies(visits_done: int) -> bool:
    """Чи цей (наступний) візит зі знижкою лояльності."""
    n, pct = settings.loyalty_every, settings.loyalty_discount_percent
    return n > 0 and pct > 0 and (visits_done + 1) % n == 0


async def create_booking_atomic(
    *,
    city_id: int,
    service_ids: list[int],
    client_telegram_id: int,
    client_name: str,
    client_phone: str,
    booking_date: date,
    start_time: time,
    payment_method: PaymentMethod,
    status: BookingStatus = BookingStatus.PENDING,
) -> Booking:
    """
    Створює запис на одну або кілька послуг підряд. Під замком і в одній
    транзакції повторно перевіряє все, що клієнт міг побачити застарілим:
    графік, минулий час, перетини з записами та заблокованим часом.
    """
    if not service_ids:
        raise BookingError("Оберіть хоча б одну послугу.")
    async with _booking_lock:
        async with session_scope() as session:
            services: list[Service] = []
            for sid in service_ids:
                svc = await session.get(Service, sid)
                if not svc or not svc.is_active:
                    raise BookingError("Одна з послуг недоступна.")
                services.append(svc)
            city = await session.get(City, city_id)
            if not city or not city.is_active:
                raise BookingError("Місто недоступне.")

            total_minutes = sum(x.duration_minutes for x in services)
            from .pricing import eff_price

            prices = [eff_price(x) for x in services]
            total_price = None if any(p is None for p in prices) else sum(prices)

            today = today_local()
            if booking_date < today or booking_date > today + timedelta(
                days=settings.max_days_ahead
            ):
                raise BookingError("Ця дата недоступна для запису.")

            hours = await get_day_hours(session, city_id, booking_date)
            if hours is None:
                raise BookingError("У цей день запис неможливий.")

            start_dt = datetime.combine(booking_date, start_time)
            end_dt = start_dt + timedelta(minutes=total_minutes)
            if (
                end_dt.date() != booking_date
                or start_time < hours[0]
                or end_dt.time() > hours[1]
            ):
                raise BookingError("Обраний час виходить за межі робочого графіка.")

            if booking_date == today and start_dt < now_local() + timedelta(
                minutes=settings.min_lead_minutes
            ):
                raise BookingError("Цей час уже недоступний. Оберіть пізніший.")

            busy = await _busy_intervals(session, booking_date, city_id)
            if not _is_free(minutes(start_time), total_minutes, busy):
                raise BookingError(
                    "Обраний час щойно зайняли. Оберіть, будь ласка, інший."
                )

            visits_done = await count_client_visits(session, client_telegram_id)
            if total_price is not None and loyalty_applies(visits_done):
                total_price = round(
                    total_price * (100 - settings.loyalty_discount_percent) / 100
                )
            elif total_price is not None:
                from . import levels  # знижка рівня клієнта (за замовчуванням 0%)

                pct = levels.pct_for(visits_done)
                if pct > 0:
                    total_price = round(total_price * (100 - pct) / 100)

            booking = Booking(
                city=city,
                service=services[0],
                service_title=(
                    " + ".join(x.name for x in services) if len(services) > 1 else None
                ),
                client_telegram_id=client_telegram_id,
                client_name=client_name,
                client_phone=client_phone,
                date=booking_date,
                start_time=start_time,
                end_time=end_dt.time(),
                price=total_price,
                payment_method=payment_method,
                status=status,
            )
            session.add(booking)
            await session.flush()
            return booking


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    return await session.get(Booking, booking_id)


async def get_client_bookings(
    session: AsyncSession,
    client_telegram_id: int,
    *,
    upcoming_only: bool = True,
) -> list[Booking]:
    q = select(Booking).where(Booking.client_telegram_id == client_telegram_id)
    if upcoming_only:
        now = now_local()
        q = q.where(
            Booking.status.in_(ACTIVE_STATUSES),
            or_(
                Booking.date > now.date(),
                and_(Booking.date == now.date(), Booking.start_time >= now.time()),
            ),
        )
    q = q.order_by(Booking.date, Booking.start_time)
    return list((await session.execute(q)).scalars().all())


async def cancel_booking_by_client(
    session: AsyncSession, booking_id: int, client_telegram_id: int
) -> Booking | None:
    booking = await session.get(Booking, booking_id)
    if not booking or booking.client_telegram_id != client_telegram_id:
        return None
    if booking.status not in ACTIVE_STATUSES:
        return None
    booking.status = BookingStatus.CANCELED
    booking.cancel_reason = "Скасовано клієнтом"
    await session.flush()
    return booking


async def mark_client_confirmed(
    session: AsyncSession, booking_id: int, client_telegram_id: int
) -> Booking | None:
    booking = await session.get(Booking, booking_id)
    if (
        not booking
        or booking.client_telegram_id != client_telegram_id
        or booking.status not in ACTIVE_STATUSES
    ):
        return None
    booking.client_confirmed = True
    await session.flush()
    return booking


def can_client_change(booking: Booking) -> bool:
    """Чи можна ще перенести запис (не пізніше min_change_hours до початку)."""
    start = datetime.combine(booking.date, booking.start_time)
    return start - now_local() >= timedelta(hours=settings.min_change_hours)


async def reschedule_booking_atomic(
    booking_id: int, client_telegram_id: int, new_date: date, new_start: time
) -> tuple[Booking, date, time]:
    """Переносить запис клієнта на інший час. Повертає (запис, стара_дата, старий_початок)."""
    async with _booking_lock:
        async with session_scope() as session:
            booking = await session.get(Booking, booking_id)
            if (
                booking is None
                or booking.client_telegram_id != client_telegram_id
                or booking.status not in ACTIVE_STATUSES
            ):
                raise BookingError("Запис не знайдено або він уже неактивний.")
            if not can_client_change(booking):
                raise BookingError(
                    f"Перенести можна не пізніше ніж за {settings.min_change_hours} год. "
                    "до візиту. Зателефонуйте майстру."
                )
            old_date, old_start = booking.date, booking.start_time
            duration = int(
                (
                    datetime.combine(old_date, booking.end_time)
                    - datetime.combine(old_date, old_start)
                ).total_seconds()
                // 60
            )
            today = today_local()
            if new_date < today or new_date > today + timedelta(days=settings.max_days_ahead):
                raise BookingError("Ця дата недоступна для запису.")
            hours = await get_day_hours(session, booking.city_id, new_date)
            if hours is None:
                raise BookingError("У цей день запис неможливий.")
            start_dt = datetime.combine(new_date, new_start)
            end_dt = start_dt + timedelta(minutes=duration)
            if (
                end_dt.date() != new_date
                or new_start < hours[0]
                or end_dt.time() > hours[1]
            ):
                raise BookingError("Обраний час виходить за межі робочого графіка.")
            if new_date == today and start_dt < now_local() + timedelta(
                minutes=settings.min_lead_minutes
            ):
                raise BookingError("Цей час уже недоступний. Оберіть пізніший.")
            busy = await _busy_intervals(
                session, new_date, booking.city_id, exclude_booking_id=booking.id
            )
            if not _is_free(minutes(new_start), duration, busy):
                raise BookingError("Цей час щойно зайняли. Оберіть, будь ласка, інший.")
            booking.date = new_date
            booking.start_time = new_start
            booking.end_time = end_dt.time()
            booking.is_notified_24h = False
            booking.is_notified_12h = False
            booking.is_notified_2h = False
            booking.is_admin_notified_soon = False
            booking.client_confirmed = False
            await session.flush()
            return booking, old_date, old_start


async def get_pending_bookings(session: AsyncSession) -> list[Booking]:
    res = await session.execute(
        select(Booking)
        .where(Booking.status == BookingStatus.PENDING)
        .order_by(Booking.date, Booking.start_time)
    )
    return list(res.scalars().all())


async def get_bookings_for_date(
    session: AsyncSession, day: date, *, city_id: int | None = None
) -> list[Booking]:
    q = (
        select(Booking)
        .where(Booking.date == day, Booking.status.in_(ACTIVE_STATUSES))
        .order_by(Booking.start_time)
    )
    if city_id is not None:
        q = q.where(Booking.city_id == city_id)
    return list((await session.execute(q)).scalars().all())


async def set_booking_status(
    session: AsyncSession,
    booking_id: int,
    new_status: BookingStatus,
    reason: str | None = None,
) -> tuple[Booking | None, bool]:
    """
    Змінює статус за дозволеними переходами.
    Повертає (запис, чи_змінилось). Повторне натискання кнопки — просто (запис, False).
    """
    booking = await session.get(Booking, booking_id)
    if not booking:
        return None, False
    allowed_from = {
        BookingStatus.CONFIRMED: (BookingStatus.PENDING,),
        BookingStatus.REJECTED: (BookingStatus.PENDING,),
        BookingStatus.CANCELED: (BookingStatus.PENDING, BookingStatus.CONFIRMED),
        BookingStatus.NO_SHOW: (BookingStatus.CONFIRMED,),
    }
    if booking.status == new_status or booking.status not in allowed_from.get(
        new_status, ()
    ):
        return booking, False
    from . import undo

    undo.record(booking.id, booking.status)
    booking.status = new_status
    if reason is not None:
        booking.cancel_reason = reason
    await session.flush()
    return booking, True


# ================== Реквізити картки ==================


async def get_card_settings(session: AsyncSession) -> CardSettings | None:
    return (await session.execute(select(CardSettings))).scalars().first()


async def update_card_settings(
    session: AsyncSession,
    *,
    card_number: str | None = None,
    card_holder: str | None = None,
    bank_name: str | None = None,
    comment: str | None = None,
) -> CardSettings:
    cs = await get_card_settings(session)
    if cs is None:
        cs = CardSettings()
        session.add(cs)
    if card_number is not None:
        cs.card_number = card_number
    if card_holder is not None:
        cs.card_holder = card_holder
    if bank_name is not None:
        cs.bank_name = bank_name
    if comment is not None:
        cs.comment = comment
    await session.flush()
    return cs


# ================== Адміністратори ==================


async def is_admin(session: AsyncSession, telegram_id: int) -> bool:
    res = await session.execute(
        select(AdminUser.id).where(
            AdminUser.telegram_id == telegram_id, AdminUser.is_active.is_(True)
        )
    )
    return res.first() is not None


async def is_superadmin(session: AsyncSession, telegram_id: int) -> bool:
    res = await session.execute(
        select(AdminUser.id).where(
            AdminUser.telegram_id == telegram_id,
            AdminUser.is_active.is_(True),
            AdminUser.is_superadmin.is_(True),
        )
    )
    return res.first() is not None


async def get_active_admin_ids(session: AsyncSession) -> list[int]:
    res = await session.execute(
        select(AdminUser.telegram_id).where(AdminUser.is_active.is_(True))
    )
    return [row[0] for row in res.all()]


async def list_admins(session: AsyncSession) -> list[AdminUser]:
    res = await session.execute(
        select(AdminUser)
        .where(AdminUser.is_active.is_(True))
        .order_by(AdminUser.id)
    )
    return list(res.scalars().all())


async def add_admin(
    session: AsyncSession, telegram_id: int, *, superadmin: bool = False
) -> AdminUser:
    admin = (
        await session.execute(
            select(AdminUser).where(AdminUser.telegram_id == telegram_id)
        )
    ).scalars().first()
    if admin:
        admin.is_active = True
        if superadmin:
            admin.is_superadmin = True
    else:
        admin = AdminUser(
            telegram_id=telegram_id, is_superadmin=superadmin, is_active=True
        )
        session.add(admin)
    await session.flush()
    return admin


async def deactivate_admin(session: AsyncSession, telegram_id: int) -> bool:
    """Суперадміна вимкнути не можна (щоб не втратити доступ до бота)."""
    admin = (
        await session.execute(
            select(AdminUser).where(AdminUser.telegram_id == telegram_id)
        )
    ).scalars().first()
    if admin is None or admin.is_superadmin:
        return False
    admin.is_active = False
    await session.flush()
    return True


# ================== Нагадування (викликає scheduler) ==================


def _notify_flag(hours_before: int):
    if hours_before == settings.notify_hours_before_1:
        return Booking.is_notified_24h, "is_notified_24h"
    if hours_before == settings.notify_hours_before_mid:
        return Booking.is_notified_12h, "is_notified_12h"
    if hours_before == settings.notify_hours_before_2:
        return Booking.is_notified_2h, "is_notified_2h"
    raise ValueError(f"Невідомий інтервал нагадування: {hours_before}")


async def get_bookings_for_notifications(
    session: AsyncSession, *, hours_before: int
) -> list[Booking]:
    """
    ПІДТВЕРДЖЕНІ записи, що почнуться приблизно через hours_before годин
    і для яких нагадування ще не надсилали.
    """
    flag_col, _ = _notify_flag(hours_before)
    return await _bookings_starting_in(
        session, flag_col, timedelta(hours=hours_before)
    )


async def get_bookings_for_admin_soon(session: AsyncSession) -> list[Booking]:
    """Підтверджені записи, що почнуться за ~admin_notify_minutes_before хв."""
    return await _bookings_starting_in(
        session,
        Booking.is_admin_notified_soon,
        timedelta(minutes=settings.admin_notify_minutes_before),
    )


async def mark_admin_notified_soon(session: AsyncSession, booking_id: int) -> None:
    booking = await session.get(Booking, booking_id)
    if booking is not None:
        booking.is_admin_notified_soon = True
        await session.flush()


async def _bookings_starting_in(
    session: AsyncSession, flag_col, delta: timedelta
) -> list[Booking]:
    now = now_local()
    target = now + delta
    window_from = target - timedelta(minutes=NOTIFY_WINDOW_MINUTES)

    rows = (
        await session.execute(
            select(Booking).where(
                Booking.status == BookingStatus.CONFIRMED,
                flag_col.is_(False),
                Booking.date >= window_from.date(),
                Booking.date <= target.date(),
            )
        )
    ).scalars().all()
    return [
        b
        for b in rows
        if window_from < datetime.combine(b.date, b.start_time) <= target
    ]


async def mark_booking_notified(
    session: AsyncSession, booking_id: int, *, hours_before: int
) -> None:
    _, attr = _notify_flag(hours_before)
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return
    setattr(booking, attr, True)
    await session.flush()


# ================== Календар: записи за період ==================


async def get_bookings_between(
    session: AsyncSession,
    start: date,
    end: date,
    *,
    statuses: tuple[BookingStatus, ...] = ACTIVE_STATUSES,
) -> list[Booking]:
    res = await session.execute(
        select(Booking)
        .where(
            Booking.date >= start,
            Booking.date <= end,
            Booking.status.in_(statuses),
        )
        .order_by(Booking.date, Booking.start_time)
    )
    return list(res.scalars().all())


# ================== Відгуки ==================


async def get_bookings_needing_review(session: AsyncSession) -> list[Booking]:
    """Підтверджені візити, що закінчились понад review_delay хвилин тому (не старші 3 днів)."""
    now = now_local()
    cutoff = now - timedelta(minutes=settings.review_delay_minutes)
    res = await session.execute(
        select(Booking).where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.is_review_requested.is_(False),
            Booking.date >= (now - timedelta(days=3)).date(),
            Booking.date <= now.date(),
        )
    )
    return [
        b
        for b in res.scalars().all()
        if datetime.combine(b.date, b.end_time) <= cutoff
    ]


async def mark_review_requested(session: AsyncSession, booking_id: int) -> None:
    booking = await session.get(Booking, booking_id)
    if booking:
        booking.is_review_requested = True
        await session.flush()


async def add_review(
    session: AsyncSession, booking_id: int, client_telegram_id: int, rating: int
) -> Review | None:
    """None — якщо запис чужий, не існує, або відгук уже залишено."""
    if not 1 <= rating <= 5:
        return None
    booking = await session.get(Booking, booking_id)
    if not booking or booking.client_telegram_id != client_telegram_id:
        return None
    exists = (
        await session.execute(select(Review).where(Review.booking_id == booking_id))
    ).scalars().first()
    if exists:
        return None
    review = Review(
        booking_id=booking_id, client_telegram_id=client_telegram_id, rating=rating
    )
    session.add(review)
    await session.flush()
    return review


async def set_review_comment(
    session: AsyncSession, booking_id: int, client_telegram_id: int, comment: str
) -> Review | None:
    review = (
        await session.execute(select(Review).where(Review.booking_id == booking_id))
    ).scalars().first()
    if not review or review.client_telegram_id != client_telegram_id:
        return None
    review.comment = comment
    await session.flush()
    return review


async def get_recent_reviews(session: AsyncSession, limit: int = 10) -> list[Review]:
    res = await session.execute(
        select(Review).order_by(Review.created_at.desc(), Review.id.desc()).limit(limit)
    )
    return list(res.scalars().all())


async def get_review_stats(session: AsyncSession) -> tuple[float, int]:
    res = await session.execute(select(func.avg(Review.rating), func.count(Review.id)))
    avg, cnt = res.one()
    return (float(avg) if avg is not None else 0.0), int(cnt or 0)


# ================== Список очікування ==================


async def add_waitlist(
    session: AsyncSession,
    client_telegram_id: int,
    city_id: int,
    day: date,
    duration_minutes: int,
) -> bool:
    """False, якщо клієнт уже чекає на цю дату."""
    exists = (
        await session.execute(
            select(WaitlistEntry).where(
                WaitlistEntry.client_telegram_id == client_telegram_id,
                WaitlistEntry.city_id == city_id,
                WaitlistEntry.date == day,
            )
        )
    ).scalars().first()
    if exists:
        exists.duration_minutes = duration_minutes
        await session.flush()
        return False
    session.add(
        WaitlistEntry(
            client_telegram_id=client_telegram_id,
            city_id=city_id,
            date=day,
            duration_minutes=duration_minutes,
        )
    )
    await session.flush()
    return True


async def pop_ready_waitlist(session: AsyncSession, day: date) -> list[WaitlistEntry]:
    """
    Записи очікування на дату, для яких тепер є вільний слот. Їх видаляємо зі списку
    (клієнт отримає одне сповіщення). Заодно чистимо минулі дати.
    """
    await session.execute(
        WaitlistEntry.__table__.delete().where(WaitlistEntry.date < today_local())
    )
    entries = (
        await session.execute(
            select(WaitlistEntry)
            .where(WaitlistEntry.date == day)
            .order_by(WaitlistEntry.created_at, WaitlistEntry.id)
        )
    ).scalars().all()
    ready: list[WaitlistEntry] = []
    for e in entries:
        slots = await get_available_time_slots(
            session,
            city_id=e.city_id,
            for_date=day,
            duration_minutes=e.duration_minutes,
        )
        if slots:
            ready.append(e)
    for e in ready:
        await session.delete(e)
    await session.flush()
    return ready


# ================== Клієнти: розсилка ==================


async def get_all_client_ids(session: AsyncSession) -> list[int]:
    res = await session.execute(select(Client.telegram_id))
    return [int(x) for x in res.scalars().all()]


# ================== Клієнти: пошук, нотатки, анкета, мова ==================


async def search_clients(session: AsyncSession, query: str, limit: int = 10) -> list[Client]:
    """Пошук за імʼям (без урахування регістру, в тому числі кирилиця) або цифрами номера."""
    q = query.strip().casefold()
    if not q:
        return []
    digits = "".join(ch for ch in q if ch.isdigit())
    all_clients = (await session.execute(select(Client).order_by(Client.name))).scalars().all()
    found = [
        c
        for c in all_clients
        if q in c.name.casefold() or (len(digits) >= 4 and digits in c.phone)
    ]
    return found[:limit]


async def get_client_by_phone(session: AsyncSession, phone: str) -> Client | None:
    """Знаходить зареєстрованого клієнта за номером (порівнює останні 9 цифр)."""
    tail = "".join(ch for ch in phone if ch.isdigit())[-9:]
    if len(tail) < 9:
        return None
    for c in (await session.execute(select(Client))).scalars().all():
        if "".join(ch for ch in c.phone if ch.isdigit())[-9:] == tail:
            return c
    return None


async def get_client_by_id(session: AsyncSession, client_id: int) -> Client | None:
    return await session.get(Client, client_id)


async def set_client_field(session: AsyncSession, telegram_id: int, **fields) -> bool:
    client = await get_client(session, telegram_id)
    if client is None:
        return False
    for key, value in fields.items():
        setattr(client, key, value)
    await session.flush()
    return True


async def get_client_history(
    session: AsyncSession, telegram_id: int, limit: int = 5
) -> list[Booking]:
    res = await session.execute(
        select(Booking)
        .where(Booking.client_telegram_id == telegram_id)
        .order_by(Booking.date.desc(), Booking.start_time.desc())
        .limit(limit)
    )
    return list(res.scalars().all())


async def get_client_lang(session: AsyncSession, telegram_id: int) -> str:
    client = await get_client(session, telegram_id)
    return client.lang if client and client.lang else "uk"


async def get_last_service_id(session: AsyncSession, telegram_id: int) -> int | None:
    res = await session.execute(
        select(Booking.service_id)
        .where(
            Booking.client_telegram_id == telegram_id,
            Booking.status.in_((BookingStatus.CONFIRMED, BookingStatus.PENDING)),
        )
        .order_by(Booking.date.desc(), Booking.start_time.desc())
        .limit(1)
    )
    return res.scalar()


async def toggle_booking_paid(session: AsyncSession, booking_id: int) -> Booking | None:
    booking = await session.get(Booking, booking_id)
    if booking is None:
        return None
    booking.is_paid = not booking.is_paid
    await session.flush()
    return booking


# ================== Закриття днів (вихідні / відпустка) ==================


async def close_days(
    session: AsyncSession, city_id: int, first: date, last: date
) -> tuple[int, list[Booking]]:
    """Робить дні вихідними. Повертає (кількість днів, записи, що вже є в цих днях)."""
    n = 0
    day = first
    while day <= last:
        await upsert_exception(session, city_id, day, is_day_off=True)
        n += 1
        day += timedelta(days=1)
    res = await session.execute(
        select(Booking)
        .where(
            Booking.date >= first,
            Booking.date <= last,
            Booking.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Booking.date, Booking.start_time)
    )
    return n, list(res.scalars().all())


# ================== Звіти та статистика ==================


async def get_bookings_in_period(
    session: AsyncSession, first: date, last: date
) -> list[Booking]:
    res = await session.execute(
        select(Booking)
        .where(Booking.date >= first, Booking.date <= last)
        .order_by(Booking.date, Booking.start_time)
    )
    return list(res.scalars().all())


async def get_all_clients(session: AsyncSession) -> list[Client]:
    return list((await session.execute(select(Client).order_by(Client.name))).scalars().all())


# ================== Повернення клієнтів і дні народження ==================


async def get_inactive_clients(session: AsyncSession, days: int) -> list[Client]:
    """Клієнти, чий останній візит був ≥ days днів тому і без майбутніх записів."""
    today = today_local()
    limit = today - timedelta(days=days)
    last_visit = (
        select(
            Booking.client_telegram_id.label("tid"),
            func.max(Booking.date).label("last_date"),
        )
        .where(Booking.status == BookingStatus.CONFIRMED)
        .group_by(Booking.client_telegram_id)
        .subquery()
    )
    future = select(Booking.client_telegram_id).where(
        Booking.date >= today, Booking.status.in_(ACTIVE_STATUSES)
    )
    res = await session.execute(
        select(Client)
        .join(last_visit, last_visit.c.tid == Client.telegram_id)
        .where(
            last_visit.c.last_date <= limit,
            Client.telegram_id.not_in(future),
            or_(
                Client.last_reengage_on.is_(None),
                Client.last_reengage_on <= today - timedelta(days=days * 2),
            ),
        )
    )
    return list(res.scalars().all())


async def get_birthday_clients(session: AsyncSession, day: date) -> list[Client]:
    res = await session.execute(select(Client).where(Client.birthday.is_not(None)))
    return [
        c
        for c in res.scalars().all()
        if c.birthday.month == day.month
        and c.birthday.day == day.day
        and c.birthday_greeted_year != day.year
    ]
