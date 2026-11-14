# init_db.py — створення таблиць і початкові дані (безпечно запускати повторно)
from __future__ import annotations

import asyncio
import logging
from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.database import init_db as create_all
from shared.database import session_scope
from shared.models import (
    AdminUser,
    CardSettings,
    City,
    Schedule,
    Service,
    ServiceCategory,
)

logger = logging.getLogger(__name__)

CITIES = ["Жовті Води"]

# Початковий графік: Пн–Сб 10:00–20:00, неділя вихідна.
# Змінюється в адмін-боті («Графік роботи»), код чіпати не потрібно.
DEFAULT_OPEN = time(10, 0)
DEFAULT_CLOSE = time(20, 0)
DEFAULT_DAYS_OFF = {6}

# (категорія, чи ціни задаються в адмінці, [(назва, хвилин, ціна|None, опис|None)])
CATALOG: list[tuple[str, str, bool, list[tuple[str, int, int | None, str | None]]]] = [
    (
        "Ручні масажі",
        "Класичні ручні масажі",
        False,
        [
            (
                "Ручний масаж спини",
                30,
                250,
                "Спина + передпліччя + криж + грудна клітка або голова",
            ),
            ("Ручний масаж тіла", 60, 400, None),
            ("Ручний масаж", 90, 600, None),
            ("Масаж обличчя ручний + маска", 30, 300, None),
            ("Реабілітація вдома", 60, 450, None),
        ],
    ),
    (
        "Апаратні масажі",
        "Апаратні масажі",
        False,
        [
            ("Пресотерапія 30 хв", 30, 250, None),
            ("Пресотерапія 60 хв", 60, 400, None),
            ("Апаратний масаж 30 хв", 30, 200, None),
            ("Апаратний масаж 60 хв", 60, 400, None),
            ("Роліковакуумний масаж 60 хв", 60, 300, None),
            ("RF-ліфтинг 30 хв", 30, 200, None),
            ("RF-ліфтинг 60 хв", 60, 300, None),
            ("RF-ліфтинг 90 хв", 90, 500, None),
            ("Ліполіз 30 хв", 30, 200, None),
            ("Ліполіз 60 хв", 60, 300, None),
            ("Кавітація 30 хв", 30, 200, None),
            ("Кавітація 60 хв", 60, 300, None),
            ("Апаратний масаж для обличчя 30 хв", 30, 250, None),
        ],
    ),
    (
        "Косметологія інʼєкційна",
        "Ціни задаються в адмін-боті.",
        True,
        [
            (name, 60, None, None)
            for name in (
                "Контурна пластика",
                "Ботулінотерапія",
                "Плазмотерапія",
                "Тредліфтінг",
                "Мезотерапія",
                "Чистка обличчя",
                "Ліпоредукція",
                "Інтимне відбілювання",
            )
        ],
    ),
    (
        "Апаратна косметологія",
        "Ціни задаються в адмін-боті.",
        True,
        [
            (name, 60, None, None)
            for name in (
                "Морфіус8 — мікроголковий ліфтинг",
                "Інтимне омолодження",
                "Фотоомолодження",
                "Видалення пігментації / постакне",
                "RF-ліфтинг",
                "Вакуумний масаж",
            )
        ],
    ),
]


async def seed_cities(session: AsyncSession) -> None:
    """Місто(а) з CITIES створюємо, а решту (напр. старі) вимикаємо, не видаляючи."""
    cities = (await session.execute(select(City))).scalars().all()
    existing = {c.name: c for c in cities}
    for name in CITIES:
        if name not in existing:
            session.add(City(name=name))
    for c in cities:
        if c.name not in CITIES and c.is_active:
            c.is_active = False
    await session.flush()


async def seed_default_schedule(session: AsyncSession) -> None:
    """Додає шаблон графіка лише тим містам, у яких його ще немає."""
    cities = (
        await session.execute(select(City).where(City.is_active.is_(True)))
    ).scalars().all()
    for city in cities:
        have = {
            s.weekday
            for s in (
                await session.execute(
                    select(Schedule).where(Schedule.city_id == city.id)
                )
            ).scalars()
        }
        for weekday in range(7):
            if weekday in have:
                continue
            session.add(
                Schedule(
                    city_id=city.id,
                    weekday=weekday,
                    start_time=DEFAULT_OPEN,
                    end_time=DEFAULT_CLOSE,
                    is_day_off=weekday in DEFAULT_DAYS_OFF,
                )
            )


async def seed_catalog(session: AsyncSession) -> None:
    """Додає лише відсутні категорії/послуги — наявні (з ціною від адміна) не чіпає."""
    for cat_name, cat_desc, editable, services in CATALOG:
        cat = (
            await session.execute(
                select(ServiceCategory).where(ServiceCategory.name == cat_name)
            )
        ).scalars().first()
        if cat is None:
            cat = ServiceCategory(
                name=cat_name, description=cat_desc, prices_editable_in_admin=editable
            )
            session.add(cat)
            await session.flush()
        have = {
            s.name
            for s in (
                await session.execute(
                    select(Service).where(Service.category_id == cat.id)
                )
            ).scalars()
        }
        for name, duration, price, description in services:
            if name not in have:
                session.add(
                    Service(
                        category_id=cat.id,
                        name=name,
                        duration_minutes=duration,
                        price=price,
                        description=description,
                    )
                )
        await session.flush()


async def seed_card_settings(session: AsyncSession) -> None:
    if (await session.execute(select(CardSettings))).scalars().first():
        return
    session.add(
        CardSettings(
            card_number="0000 0000 0000 0000",
            card_holder="Імʼя Прізвище",
            bank_name="Назва банку",
            comment="Реквізити можна змінити в адмін-боті.",
        )
    )


async def seed_superadmin(session: AsyncSession) -> None:
    if not settings.superadmin_telegram_id:
        return
    admin = (
        await session.execute(
            select(AdminUser).where(
                AdminUser.telegram_id == settings.superadmin_telegram_id
            )
        )
    ).scalars().first()
    if admin is None:
        session.add(
            AdminUser(
                telegram_id=settings.superadmin_telegram_id,
                is_superadmin=True,
                is_active=True,
            )
        )
    else:
        admin.is_superadmin = True
        admin.is_active = True


async def bootstrap() -> None:
    """Таблиці + початкові дані. Викликається і вручну, і з run.py при старті."""
    await create_all()
    async with session_scope() as session:
        await seed_cities(session)
        await seed_default_schedule(session)
        await seed_catalog(session)
        await seed_card_settings(session)
        await seed_superadmin(session)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(bootstrap())
    print("База даних готова:", settings.database_url)
