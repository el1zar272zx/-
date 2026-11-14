# shared/salon.py — «Про салон»: адреса, телефон, карта, фото, часті питання
from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import session_scope
from .models import FaqItem, SalonInfo, SalonPhoto
from .utils import esc

logger = logging.getLogger(__name__)

KEYS = ("address", "phone", "map_url", "pay_url", "howto", "instagram")
_cache: dict[str, str] = {}

DEFAULT_FAQ = [
    (
        "Скільки триває сеанс?",
        "Тривалість вказана біля кожної послуги в розділі «Послуги та ціни».",
    ),
    (
        "Як скасувати або перенести запис?",
        "У розділі «Мої записи» оберіть запис і натисніть «Перенести» або «Скасувати». "
        "Переносити можна не пізніше ніж за кілька годин до візиту.",
    ),
    (
        "Що взяти з собою?",
        "Зазвичай достатньо себе й гарного настрою. Якщо потрібно щось особливе, "
        "ми напишемо в нагадуванні перед візитом.",
    ),
    (
        "Чи є протипоказання?",
        "Так, вони залежать від процедури. Перед першим записом бот пропонує коротку анкету, "
        "а майстер уточнить деталі перед сеансом.",
    ),
]


async def load_salon_info() -> None:
    """Підтягує налаштування з бази у памʼять (з .env як запасний варіант)."""
    async with session_scope() as session:
        rows = (await session.execute(select(SalonInfo))).scalars().all()
    data = {r.key: r.value for r in rows}
    _cache.clear()
    _cache["address"] = data.get("address", settings.salon_address)
    _cache["phone"] = data.get("phone", settings.salon_phone)
    _cache["map_url"] = data.get("map_url", settings.salon_map_url)
    _cache["pay_url"] = data.get("pay_url", "")
    _cache["howto"] = data.get("howto", "")
    _cache["instagram"] = data.get("instagram", "")


def info(key: str) -> str:
    """Поточне значення (синхронно, з памʼяті). До завантаження береться з .env."""
    if not _cache:
        return {
            "address": settings.salon_address,
            "phone": settings.salon_phone,
            "map_url": settings.salon_map_url,
        }.get(key, "")
    return _cache.get(key, "")


async def set_info(key: str, value: str) -> None:
    if key not in KEYS:
        raise ValueError(key)
    async with session_scope() as session:
        row = await session.get(SalonInfo, key)
        if row is None:
            session.add(SalonInfo(key=key, value=value))
        else:
            row.value = value
    await load_salon_info()


def address_block() -> str:
    """Рядок адреси для нагадувань (порожній, якщо адресу не вказано)."""
    if not info("address"):
        return ""
    text = f"\n\n📍 {esc(info('address'))}"
    if info("map_url"):
        text += f"\n{esc(info('map_url'))}"
    if info("howto"):
        text += f"\n🚪 {esc(info('howto'))}"
    return text


# ---------- Фото ----------


async def add_photo(session: AsyncSession, data: bytes, caption: str | None) -> SalonPhoto:
    photo = SalonPhoto(data=data, caption=(caption or None))
    session.add(photo)
    await session.flush()
    return photo


async def list_photos(session: AsyncSession) -> list[SalonPhoto]:
    return list((await session.execute(select(SalonPhoto).order_by(SalonPhoto.id))).scalars().all())


async def delete_photo(session: AsyncSession, photo_id: int) -> bool:
    res = await session.execute(delete(SalonPhoto).where(SalonPhoto.id == photo_id))
    return bool(res.rowcount)


# ---------- Часті питання ----------


async def list_faq(session: AsyncSession) -> list[FaqItem]:
    items = list((await session.execute(select(FaqItem).order_by(FaqItem.id))).scalars().all())
    if not items:
        marker = await session.get(SalonInfo, "faq_seeded")
        if marker is None:  # перший показ: заповнюємо стандартними відповідями
            for q, a in DEFAULT_FAQ:
                session.add(FaqItem(question=q, answer=a))
            session.add(SalonInfo(key="faq_seeded", value="1"))
            await session.flush()
            items = list(
                (await session.execute(select(FaqItem).order_by(FaqItem.id))).scalars().all()
            )
    return items


async def ensure_faq_v2() -> None:
    """Одноразово додає розширений список запитань (масаж і косметологія). Наявні питання не чіпає."""
    from .faq_data import FAQ_V2

    async with session_scope() as session:
        if await session.get(SalonInfo, "faq_v2") is not None:
            return
        items = (await session.execute(select(FaqItem))).scalars().all()
        if not items and await session.get(SalonInfo, "faq_seeded") is None:
            for q, a in DEFAULT_FAQ:
                session.add(FaqItem(question=q, answer=a))
            session.add(SalonInfo(key="faq_seeded", value="1"))
        have = {i.question for i in items}
        for q, a in FAQ_V2:
            if q not in have:
                session.add(FaqItem(question=q[:200], answer=a))
        session.add(SalonInfo(key="faq_v2", value="1"))


async def add_faq(session: AsyncSession, question: str, answer: str) -> FaqItem:
    item = FaqItem(question=question[:200], answer=answer)
    session.add(item)
    await session.flush()
    return item


async def delete_faq(session: AsyncSession, faq_id: int) -> bool:
    res = await session.execute(delete(FaqItem).where(FaqItem.id == faq_id))
    return bool(res.rowcount)
