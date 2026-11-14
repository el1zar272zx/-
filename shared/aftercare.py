# shared/aftercare.py — рекомендації до і після процедур, нагадування «вже можна»
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import session_scope
from .models import (
    AftercareNotice,
    AftercareRule,
    Booking,
    BookingStatus,
    SalonInfo,
    Service,
)
from .utils import esc, now_local

logger = logging.getLogger(__name__)

# Типові шаблони. Це загальні орієнтири: майстер перевіряє й змінює їх під свій протокол
# у розділі «Послуги» → послуга → «🌿 Рекомендації».
_BOTOX = [
    ("лягати й нахилятися головою вниз", 4),
    ("торкатися, тиснути й масажувати зону уколів", 24),
    ("вживати алкоголь", 24),
    ("інтенсивний спорт і сильні навантаження", 24),
    ("сауна, лазня, гарячий душ і ванна", 48),
]
_FILLER_LIPS = [
    ("вживати алкоголь", 24),
    ("інтенсивний спорт", 48),
    ("напружувати губи (широка посмішка, голосний сміх)", 48),
    ("сауна, лазня, гаряча ванна", 72),
    ("поцілунки й візит до стоматолога", 168),
]
_MASSAGE = [("гаряча ванна й сауна", 2)]

DEFAULTS: list[tuple[tuple[str, ...], list[tuple[str, int]], str | None]] = [
    (
        ("ботокс", "ботулін", "botox", "диспорт", "ксеомін"),
        _BOTOX,
        "За добу до процедури не вживайте алкоголь. Прийдіть без макіяжу або зніміть його на місці. "
        "Про ліки (особливо розріджувачі крові) скажіть майстру.",
    ),
    (
        ("губ", "філер", "контурн", "гіалурон"),
        _FILLER_LIPS,
        "За добу до процедури не вживайте алкоголь. Про ліки (особливо розріджувачі крові) "
        "та герпес скажіть майстру.",
    ),
    (
        ("масаж",),
        _MASSAGE,
        "Не їжте плотно за 1–1,5 години до сеансу. Прийдіть за 5 хвилин, щоб не поспішати.",
    ),
]


def hours_label(hours: int) -> str:
    if hours >= 24 and hours % 24 == 0:
        days = hours // 24
        return f"{days} д."
    return f"{hours} год."


async def seed_aftercare_defaults() -> None:
    """Одноразово додає типові рекомендації до послуг, назви яких збігаються з шаблонами."""
    async with session_scope() as session:
        if await session.get(SalonInfo, "aftercare_seeded") is not None:
            return
        services = (await session.execute(select(Service))).scalars().all()
        for svc in services:
            name = svc.name.casefold()
            for keywords, rules, prep in DEFAULTS:
                if any(k in name for k in keywords):
                    for text, hours in rules:
                        session.add(AftercareRule(service_id=svc.id, text=text, hours=hours))
                    if prep and not svc.prep:
                        svc.prep = prep
                    break
        session.add(SalonInfo(key="aftercare_seeded", value="1"))


# Розширені шаблони для апаратних і косметологічних процедур (загальні орієнтири, майстер їх уточнює)
_SUN = ("сонце й солярій", 72)
EXTRA_DEFAULTS: list[tuple[tuple[str, ...], list[tuple[str, int]], str]] = [
    (
        ("пресотерап", "кавітац", "ліполіз", "ліпоредукц", "роліковак", "вакуумн"),
        [("сауна, лазня, гаряча ванна", 24), ("інтенсивне навантаження", 24)],
        "За 1–2 години до процедури не їжте плотно, випийте води. Про вагітність, варикоз, "
        "хвороби нирок і серця обовʼязково скажіть майстру.",
    ),
    (
        ("чистк", "пілінг"),
        [("декоративна косметика", 12), ("сауна й гаряча ванна", 48), _SUN, ("здирати кірочки чи лущення", 72)],
        "Прийдіть без макіяжу. Про ліки (зокрема ретиноїди), герпес і алергії скажіть майстру.",
    ),
    (
        ("мезотерап", "плазмотерап", "біоревіт", "тредліфт"),
        [("торкатися зони уколів і наносити макіяж", 12), ("вживати алкоголь", 24), ("сауна, лазня, інтенсивний спорт", 48), _SUN],
        "За добу до процедури не вживайте алкоголь. Про ліки (особливо розріджувачі крові) скажіть майстру.",
    ),
    (
        ("морфіус", "мікроголк", "rf-ліфтинг", "фотоомолод", "пігментац", "постакне"),
        [("сауна, лазня, гаряча ванна", 48), ("агресивні скраби й кислоти", 72), ("сонце й солярій", 168)],
        "За кілька днів до процедури уникайте засмаги. Про ліки, герпес і шкірні захворювання скажіть майстру.",
    ),
    (
        ("інтимн",),
        [("сауна, ванна, басейн", 48), ("інтенсивний спорт", 24)],
        "Про ліки, хвороби й вагітність скажіть майстру до початку процедури.",
    ),
]


async def seed_aftercare_v2() -> None:
    """Одноразово додає розширені шаблони послугам, у яких ще немає жодних правил."""
    async with session_scope() as session:
        if await session.get(SalonInfo, "aftercare_seeded_v2") is not None:
            return
        services = (await session.execute(select(Service))).scalars().all()
        for svc in services:
            have = (
                await session.execute(select(AftercareRule.id).where(AftercareRule.service_id == svc.id).limit(1))
            ).first()
            if have:
                continue
            name = svc.name.casefold()
            for keywords, rules, prep in EXTRA_DEFAULTS:
                if any(k in name for k in keywords):
                    for text, hours in rules:
                        session.add(AftercareRule(service_id=svc.id, text=text, hours=hours))
                    if not svc.prep:
                        svc.prep = prep
                    break
        session.add(SalonInfo(key="aftercare_seeded_v2", value="1"))


# ---------- Редагування правил ----------


async def list_rules(session: AsyncSession, service_id: int) -> list[AftercareRule]:
    res = await session.execute(
        select(AftercareRule)
        .where(AftercareRule.service_id == service_id)
        .order_by(AftercareRule.hours, AftercareRule.id)
    )
    return list(res.scalars().all())


async def add_rule(session: AsyncSession, service_id: int, text: str, hours: int) -> AftercareRule:
    rule = AftercareRule(service_id=service_id, text=text[:200], hours=hours)
    session.add(rule)
    await session.flush()
    return rule


async def delete_rule(session: AsyncSession, rule_id: int) -> int | None:
    rule = await session.get(AftercareRule, rule_id)
    if rule is None:
        return None
    service_id = rule.service_id
    await session.delete(rule)
    await session.flush()
    return service_id


# ---------- Планування й відправка ----------


def format_start(booking: Booking, rules: list[AftercareRule]) -> str:
    lines = [f"🌿 <b>Рекомендації після процедури</b> ({esc(booking.title)})", "", "Не можна:"]
    lines += [f"• {esc(r.text)} — {hours_label(r.hours)}" for r in rules]
    lines += ["", "Коли мине час, я напишу, що вже можна. Якщо щось турбує, зателефонуйте майстру."]
    return "\n".join(lines)


def format_free(rule: AftercareRule) -> str:
    return f"✅ Минуло {hours_label(rule.hours)}: тепер вже можна — {esc(rule.text)}."


async def schedule_new_aftercare(session: AsyncSession) -> int:
    """Для завершених підтверджених записів створює повідомлення. Повертає кількість записів."""
    now = now_local().replace(tzinfo=None)
    res = await session.execute(
        select(Booking).where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.aftercare_scheduled.is_(False),
            Booking.date <= now.date(),
        )
    )
    n = 0
    for b in res.scalars().all():
        end = datetime.combine(b.date, b.end_time)
        if end > now:
            continue
        b.aftercare_scheduled = True
        rules = await list_rules(session, b.service_id) if b.service_id else []
        if not rules:
            continue
        session.add(AftercareNotice(booking_id=b.id, kind="start", due_at=end))
        for r in rules:
            session.add(
                AftercareNotice(
                    booking_id=b.id,
                    rule_id=r.id,
                    kind="free",
                    due_at=end + timedelta(hours=r.hours),
                )
            )
        n += 1
    return n


async def due_notices(session: AsyncSession) -> list[tuple[AftercareNotice, Booking, AftercareRule | None, list[AftercareRule]]]:
    now = now_local().replace(tzinfo=None)
    notices = (
        await session.execute(
            select(AftercareNotice).where(
                AftercareNotice.sent.is_(False), AftercareNotice.due_at <= now
            )
        )
    ).scalars().all()
    out = []
    for n in notices:
        booking = await session.get(Booking, n.booking_id)
        rule = await session.get(AftercareRule, n.rule_id) if n.rule_id else None
        rules = await list_rules(session, booking.service_id) if booking and n.kind == "start" else []
        out.append((n, booking, rule, rules))
    return out


async def mark_notice_sent(session: AsyncSession, notice_id: int) -> None:
    n = await session.get(AftercareNotice, notice_id)
    if n is not None:
        n.sent = True


async def cleanup_old_notices(session: AsyncSession, days: int = 60) -> None:
    limit = now_local().replace(tzinfo=None) - timedelta(days=days)
    await session.execute(
        delete(AftercareNotice).where(AftercareNotice.sent.is_(True), AftercareNotice.due_at < limit)
    )
