# admin_bot/handlers/pro.py — «🧰 Інструменти»: швидкий запис, «зайнята», шаблони, мітки, завдання,
# історія клієнта, поради, топ питань, рівні, бібліотека «Корисне», ідеї клієнтів, журнал дій (лише власник)
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, func, select

from shared import actionlog, emptyhours, jobs2, levels, library, quickbook
from shared.database import session_scope
from shared.models import (
    AdminTask,
    BlockedSlot,
    Booking,
    BookingStatus,
    Client,
    ClientQuestion,
    ConsentRecord,
    FaqItem,
    PaymentMethod,
    QuickReply,
    Service,
    Suggestion,
    SuggestionVote,
)
from shared.notifications_soft import STATUS_UA, format_booking_card
from shared.services import (
    BookingError,
    create_booking_atomic,
    get_active_cities,
    get_client_by_id,
    get_client_by_phone,
    get_client_history,
    phone_pseudo_id,
    search_clients,
)
from shared.tg import safe_edit
from shared.utils import esc, fmt_date_long, fmt_time, now_local, parse_cb_int, today_local

from ..keyboards import A_TOOLS, back, booking_actions_kb, btn, cancel_input_kb, kb
from ..notify import send_to_client
from ..states import (
    AdviceStates,
    AnswerStates,
    FaqFromQStates,
    LevelStates,
    LibStates,
    QuickStates,
    TagStates,
    TaskStates,
    TplStates,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_pro")

MENU = "pro_menu"
TAG_PRESETS = ("VIP", "Алергія", "Чутлива шкіра", "Не дзвонити", "Ранковий", "Друг/родина")
DEFAULT_TEMPLATES = (
    "Дякую за запис! Чекаю на вас 🌿",
    "Так, час вільний. Напишіть, будь ласка, зручну дату.",
    "На жаль, цей час уже зайнятий. Можу запропонувати інший: напишіть, коли вам зручно.",
    "Будь ласка, зателефонуйте мені, так буде швидше.",
)


# ---------- Меню ----------


def _menu_kb(is_super: bool):
    rows = [
        [btn("⚡ Швидкий запис текстом", "pro_qb")],
        [btn("⏳ Я зайнята найближчі години", "pro_busy")],
        [btn("📋 Мої завдання", "pro_tasks"), btn("💬 Шаблони відповідей", "pro_tpl")],
        [btn("❓ Топ питань клієнтів", "pro_topq"), btn("💡 Ідеї від клієнтів", "pro_sug")],
        [btn("🏅 Рівні клієнтів", "pro_lvl"), btn("📚 Бібліотека «Корисне»", "pro_lib")],
        [btn("🔍 Порожні години", "eh_preview")],
        [btn("📸 Пост для Instagram", "ig_menu"), btn("🎵 Плейлисти", "pl_menu")],
    ]
    if is_super:
        rows.append([btn("📜 Журнал дій (лише власник)", "pro_log")])
    rows.append([back("adm_menu")])
    return kb(rows)


MENU_TEXT = "🧰 <b>Інструменти</b>\nДодаткові можливості для щоденної роботи:"


@router.message(F.text == A_TOOLS)
async def bar_tools(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    await msg.answer(MENU_TEXT, reply_markup=_menu_kb(is_super))


@router.callback_query(F.data == MENU)
async def cb_tools(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    await call.answer()
    await safe_edit(call, MENU_TEXT, _menu_kb(is_super))


# ---------- Швидкий запис текстом ----------

QB_HELP = (
    "⚡ <b>Швидкий запис</b>\n"
    "Напишіть одним повідомленням, наприклад:\n"
    "<code>Анна, масаж спини, завтра 15:00, +380501234567</code>\n\n"
    "Я розберу імʼя, послугу, день, час і телефон та покажу, що вийшло. "
    "Запис буде створено лише після вашого підтвердження."
)


@router.callback_query(F.data == "pro_qb")
async def qb_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(QuickStates.text)
    await call.answer()
    await safe_edit(call, QB_HELP, cancel_input_kb(MENU))


async def _services() -> list[Service]:
    async with session_scope() as session:
        res = await session.execute(select(Service).where(Service.is_active.is_(True)).order_by(Service.id))
        return list(res.scalars().all())


def _qb_preview(d: dict, svc_name: str) -> str:
    day = date.fromisoformat(d["day"])
    return (
        "⚡ <b>Перевірте запис</b>\n"
        f"👤 {esc(d['name'])} · {esc(d['phone'])}\n"
        f"💆 {esc(svc_name)}\n"
        f"📅 {fmt_date_long(day)}, {d['start']}"
    )


@router.message(QuickStates.text, F.text)
async def qb_text(msg: Message, state: FSMContext) -> None:
    services = await _services()
    p = quickbook.parse(msg.text, services, today_local())
    phone = p.phone
    name = p.name
    if not phone and name:
        async with session_scope() as session:
            found = await search_clients(session, name)
        if len(found) == 1:
            phone = found[0].phone
            name = found[0].name
    missing = []
    if not name:
        missing.append("імʼя")
    if not phone:
        missing.append("телефон")
    if p.service_id is None and not p.candidates:
        missing.append("послугу")
    if p.day is None:
        missing.append("день")
    if p.start is None:
        missing.append("час")
    if missing:
        await msg.answer(
            "Не вистачає: " + ", ".join(missing) + ".\nНапишіть повідомлення ще раз повністю.\n\n" + QB_HELP,
            reply_markup=cancel_input_kb(MENU),
        )
        return
    data = {
        "name": name,
        "phone": phone,
        "day": p.day.isoformat(),
        "start": p.start.strftime("%H:%M"),
        "service_id": p.service_id,
    }
    await state.update_data(qb=data)
    if p.service_id is None:
        names = {s.id: s.name for s in services}
        rows = [[btn(names[i][:50], f"pro_qbs_{i}")] for i in (p.candidates or []) if i in names][:8]
        rows.append([btn("✖️ Скасувати", MENU)])
        await msg.answer("Яка саме послуга?", reply_markup=kb(rows))
        return
    await msg.answer(_qb_preview(data, p.service_name or ""), reply_markup=_qb_confirm_kb())


def _qb_confirm_kb():
    return kb([[btn("✅ Створити запис", "pro_qbok")], [btn("✖️ Скасувати", MENU)]])


@router.callback_query(F.data.startswith("pro_qbs_"))
async def qb_pick_service(call: CallbackQuery, state: FSMContext) -> None:
    sid = parse_cb_int(call.data, "pro_qbs_")
    qb = (await state.get_data()).get("qb")
    if sid is None or not qb:
        await call.answer("Почніть спочатку.", show_alert=True)
        return
    qb["service_id"] = sid
    await state.update_data(qb=qb)
    async with session_scope() as session:
        svc = await session.get(Service, sid)
    await call.answer()
    await safe_edit(call, _qb_preview(qb, svc.name if svc else ""), _qb_confirm_kb())


@router.callback_query(F.data == "pro_qbok")
async def qb_create(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    qb = (await state.get_data()).get("qb")
    if not qb or not qb.get("service_id"):
        await call.answer("Почніть спочатку.", show_alert=True)
        return
    try:
        day = date.fromisoformat(qb["day"])
        start = time.fromisoformat(qb["start"])
    except ValueError:
        await call.answer("Почніть спочатку.", show_alert=True)
        return
    async with session_scope() as session:
        cities = await get_active_cities(session)
        registered = await get_client_by_phone(session, qb["phone"])
    tg_id = registered.telegram_id if registered else phone_pseudo_id(qb["phone"])
    try:
        booking = await create_booking_atomic(
            city_id=cities[0].id,
            service_ids=[qb["service_id"]],
            client_telegram_id=tg_id,
            client_name=qb["name"],
            client_phone=qb["phone"],
            booking_date=day,
            start_time=start,
            payment_method=PaymentMethod.CASH,
            status=BookingStatus.CONFIRMED,
        )
    except BookingError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await call.answer("Запис створено")
    if registered:
        sent = await send_to_client(
            client_bot,
            booking,
            "✅ <b>Майстер записала вас на прийом.</b>\n\n" + format_booking_card(booking),
            with_ics=True,
        )
        note = "📨 Клієнту надіслано повідомлення." if sent else "⚠️ Клієнту не доставлено. Повідомте його самі."
    else:
        note = "ℹ️ Клієнт ще не користується ботом, тому повідомлення йому не надсилаються."
    await safe_edit(
        call,
        "✅ Клієнта записано.\n\n" + format_booking_card(booking, for_admin=True) + "\n\n" + note,
        booking_actions_kb(booking),
    )


# ---------- «Я зайнята найближчі години» ----------


@router.callback_query(F.data == "pro_busy")
async def busy_menu(call: CallbackQuery) -> None:
    await call.answer()
    await safe_edit(
        call,
        "⏳ <b>Я зайнята</b>\nНа скільки годин закрити запис? Клієнти не зможуть обрати цей час.",
        kb(
            [
                [btn("1 година", "pro_busy_1"), btn("2 години", "pro_busy_2")],
                [btn("3 години", "pro_busy_3"), btn("4 години", "pro_busy_4")],
                [btn("До кінця дня", "pro_busy_0")],
                [back(MENU)],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^pro_busy_\d$"))
async def busy_set(call: CallbackQuery) -> None:
    hours = int(call.data.removeprefix("pro_busy_"))
    now = now_local()
    day = now.date()
    start = time(now.hour, now.minute)
    end = time(23, 59) if hours == 0 else (now + timedelta(hours=hours)).time()
    if hours and (now + timedelta(hours=hours)).date() != day:
        end = time(23, 59)
    if start >= end:
        await call.answer("На сьогодні вже пізно.", show_alert=True)
        return
    async with session_scope() as session:
        session.add(BlockedSlot(date=day, start_time=start, end_time=end, note="Зайнята (швидка позначка)"))
        res = await session.execute(
            select(Booking).where(
                Booking.date == day,
                Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]),
                Booking.start_time < end,
                Booking.end_time > start,
            )
        )
        clash = list(res.scalars().all())
    await call.answer("Готово")
    text = f"⏳ Час {fmt_time(start)}–{fmt_time(end)} закрито для запису."
    if clash:
        text += "\n\n⚠️ У цей проміжок уже є записи (вони не скасовані):\n" + "\n".join(
            f"• {fmt_time(b.start_time)} {esc(b.client_name)}" for b in clash
        )
    text += "\n\nЗнімається в «🗓 Графік роботи» → блокування часу."
    await safe_edit(call, text, kb([[back(MENU)]]))


# ---------- Шаблони відповідей ----------


async def _ensure_templates() -> list[QuickReply]:
    from shared.models import SalonInfo

    async with session_scope() as session:
        if await session.get(SalonInfo, "tpl_seeded") is None:
            for t in DEFAULT_TEMPLATES:
                session.add(QuickReply(text=t))
            session.add(SalonInfo(key="tpl_seeded", value="1"))
    async with session_scope() as session:
        return list((await session.execute(select(QuickReply).order_by(QuickReply.id))).scalars().all())


@router.callback_query(F.data == "pro_tpl")
async def tpl_list(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tpls = await _ensure_templates()
    rows = [[btn(f"🗑 {t.text[:45]}", f"pro_tpld_{t.id}")] for t in tpls]
    rows.append([btn("➕ Новий шаблон", "pro_tpln")])
    rows.append([back(MENU)])
    await call.answer()
    await safe_edit(
        call,
        "💬 <b>Шаблони відповідей</b>\nВони зʼявляються під час відповіді клієнту. Натисніть, щоб видалити:",
        kb(rows),
    )


@router.callback_query(F.data.startswith("pro_tpld_"))
async def tpl_delete(call: CallbackQuery) -> None:
    tid = parse_cb_int(call.data, "pro_tpld_")
    if tid is not None:
        async with session_scope() as session:
            await session.execute(delete(QuickReply).where(QuickReply.id == tid))
    await call.answer("Видалено")
    tpls = await _ensure_templates()
    rows = [[btn(f"🗑 {t.text[:45]}", f"pro_tpld_{t.id}")] for t in tpls]
    rows += [[btn("➕ Новий шаблон", "pro_tpln")], [back(MENU)]]
    await safe_edit(call, "💬 <b>Шаблони відповідей</b>\nНатисніть, щоб видалити:", kb(rows))


@router.callback_query(F.data == "pro_tpln")
async def tpl_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TplStates.text)
    await call.answer()
    await safe_edit(call, "Напишіть текст шаблону (до 500 символів):", cancel_input_kb("pro_tpl"))


@router.message(TplStates.text, F.text)
async def tpl_save(msg: Message, state: FSMContext) -> None:
    async with session_scope() as session:
        session.add(QuickReply(text=msg.text.strip()[:500]))
    await state.clear()
    await msg.answer("Шаблон збережено ✅", reply_markup=kb([[btn("💬 До шаблонів", "pro_tpl")], [back(MENU)]]))


@router.callback_query(F.data.startswith("pro_use_"))
async def tpl_use(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    tid = parse_cb_int(call.data, "pro_use_")
    client_tg = (await state.get_data()).get("client_tg")
    async with session_scope() as session:
        tpl = await session.get(QuickReply, tid) if tid is not None else None
    if tpl is None or client_tg is None:
        await call.answer("Почніть відповідь спочатку.", show_alert=True)
        return
    await state.clear()
    if client_bot is None or client_tg <= 0:
        await call.answer("Клієнт не користується ботом. Зателефонуйте йому.", show_alert=True)
        return
    try:
        await client_bot.send_message(client_tg, f"💬 <b>Відповідь майстра</b>\n\n{esc(tpl.text)}")
    except TelegramAPIError:
        await call.answer("Не вдалося надіслати: клієнт міг заблокувати бота.", show_alert=True)
        return
    await call.answer("Надіслано ✅")
    await safe_edit(call, "Відповідь надіслано ✅", kb([[back("adm_menu")]]))


# ---------- Мітки клієнтів ----------


def _tags_of(client: Client) -> list[str]:
    return [t.strip() for t in (client.tags or "").split(",") if t.strip()]


async def _tag_screen(call_or_msg, client_id: int, edit: bool) -> None:
    async with session_scope() as session:
        client = await get_client_by_id(session, client_id)
    if client is None:
        return
    cur = {t.casefold() for t in _tags_of(client)}
    rows = []
    for i, name in enumerate(TAG_PRESETS):
        mark = "✅ " if name.casefold() in cur else ""
        rows.append([btn(f"{mark}{name}", f"pro_tgp_{client_id}_{i}")])
    custom = [t for t in _tags_of(client) if t.casefold() not in {p.casefold() for p in TAG_PRESETS}]
    for t in custom:
        rows.append([btn(f"🗑 {t}", f"pro_tgx_{client_id}_{_tags_of(client).index(t)}")])
    rows.append([btn("➕ Своя мітка", f"pro_tgc_{client_id}")])
    rows.append([back(f"acl_{client_id}")])
    text = f"🏷 <b>Мітки: {esc(client.name)}</b>\nШукати за міткою можна в «🔎 Клієнти»: напишіть #VIP."
    if edit:
        await safe_edit(call_or_msg, text, kb(rows))
    else:
        await call_or_msg.answer(text, reply_markup=kb(rows))


async def _save_tags(client_id: int, tags: list[str]) -> None:
    async with session_scope() as session:
        client = await session.get(Client, client_id)
        if client is not None:
            client.tags = ", ".join(tags)[:300] or None


@router.callback_query(F.data.regexp(r"^pro_tag_\d+$"))
async def tag_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    cid = parse_cb_int(call.data, "pro_tag_")
    await call.answer()
    if cid is not None:
        await _tag_screen(call, cid, True)


@router.callback_query(F.data.regexp(r"^pro_tgp_\d+_\d+$"))
async def tag_toggle(call: CallbackQuery) -> None:
    _, _, cid, idx = call.data.split("_")
    cid, idx = int(cid), int(idx)
    async with session_scope() as session:
        client = await get_client_by_id(session, cid)
    if client is None or idx >= len(TAG_PRESETS):
        await call.answer()
        return
    name = TAG_PRESETS[idx]
    tags = _tags_of(client)
    if name.casefold() in [t.casefold() for t in tags]:
        tags = [t for t in tags if t.casefold() != name.casefold()]
    else:
        tags.append(name)
    await _save_tags(cid, tags)
    await call.answer()
    await _tag_screen(call, cid, True)


@router.callback_query(F.data.regexp(r"^pro_tgx_\d+_\d+$"))
async def tag_remove(call: CallbackQuery) -> None:
    _, _, cid, idx = call.data.split("_")
    cid, idx = int(cid), int(idx)
    async with session_scope() as session:
        client = await get_client_by_id(session, cid)
    if client is not None:
        tags = _tags_of(client)
        if idx < len(tags):
            tags.pop(idx)
            await _save_tags(cid, tags)
    await call.answer()
    await _tag_screen(call, cid, True)


@router.callback_query(F.data.regexp(r"^pro_tgc_\d+$"))
async def tag_custom_start(call: CallbackQuery, state: FSMContext) -> None:
    cid = parse_cb_int(call.data, "pro_tgc_")
    await state.set_state(TagStates.text)
    await state.update_data(cid=cid)
    await call.answer()
    await safe_edit(call, "Напишіть мітку (одне-два слова):", cancel_input_kb(f"pro_tag_{cid}"))


@router.message(TagStates.text, F.text)
async def tag_custom_save(msg: Message, state: FSMContext) -> None:
    cid = (await state.get_data()).get("cid")
    tag = msg.text.strip().lstrip("#").replace(",", " ")[:30].strip()
    await state.clear()
    if cid is None or not tag:
        return
    async with session_scope() as session:
        client = await get_client_by_id(session, cid)
    if client is not None:
        tags = _tags_of(client)
        if tag.casefold() not in [t.casefold() for t in tags]:
            tags.append(tag)
        await _save_tags(cid, tags)
    await _tag_screen(msg, cid, False)


# ---------- Історія клієнта ----------


@router.callback_query(F.data.regexp(r"^pro_tl_\d+$"))
async def timeline(call: CallbackQuery) -> None:
    cid = parse_cb_int(call.data, "pro_tl_")
    async with session_scope() as session:
        client = await get_client_by_id(session, cid) if cid is not None else None
        if client is None:
            await call.answer("Клієнта не знайдено.", show_alert=True)
            return
        history = await get_client_history(session, client.telegram_id, 30)
        consents = (
            await session.execute(
                select(ConsentRecord).where(ConsentRecord.client_telegram_id == client.telegram_id)
            )
        ).scalars().all()
    lines = [f"🕒 <b>Історія: {esc(client.name)}</b>"]
    if client.created_at:
        lines.append(f"З нами з {client.created_at:%d.%m.%Y}")
    done = sum(1 for b in history if b.status == BookingStatus.CONFIRMED)
    lines.append(f"Візитів у списку: {done} із {len(history)} записів")
    if consents:
        lines.append(f"📝 Підписаних згод: {len(consents)}")
    lines.append("")
    for b in history:
        lines.append(f"• {b.date:%d.%m.%Y} {fmt_time(b.start_time)} · {esc(b.title)} · {STATUS_UA[b.status]}")
        if b.visit_note:
            lines.append(f"   🗒 {esc(b.visit_note)}")
        if b.flags:
            lines.append(f"   🚩 {esc(b.flags)}")
    if client.notes:
        lines += ["", f"📝 {esc(client.notes)}"]
    await call.answer()
    await safe_edit(call, "\n".join(lines)[:3900], kb([[back(f"acl_{cid}")]]))


# ---------- Персональна порада клієнту ----------


@router.callback_query(F.data.regexp(r"^pro_adv_\d+$"))
async def advice_start(call: CallbackQuery, state: FSMContext) -> None:
    cid = parse_cb_int(call.data, "pro_adv_")
    async with session_scope() as session:
        client = await get_client_by_id(session, cid) if cid is not None else None
    if client is None or client.telegram_id <= 0:
        await call.answer("Цей клієнт не користується ботом.", show_alert=True)
        return
    await state.set_state(AdviceStates.text)
    await state.update_data(cid=cid)
    await call.answer()
    await safe_edit(
        call,
        f"💌 Напишіть персональну пораду для {esc(client.name)} (наприклад, після процедури). "
        "Я надішлю її від імені майстра:",
        cancel_input_kb(f"acl_{cid}"),
    )


@router.message(AdviceStates.text, F.text)
async def advice_send(msg: Message, state: FSMContext, client_bot: Bot | None = None) -> None:
    cid = (await state.get_data()).get("cid")
    await state.clear()
    async with session_scope() as session:
        client = await get_client_by_id(session, cid) if cid is not None else None
    if client is None or client_bot is None:
        await msg.answer("Не вдалося надіслати.")
        return
    try:
        await client_bot.send_message(
            client.telegram_id, f"💌 <b>Порада від майстра</b>\n\n{esc(msg.text.strip())}"
        )
    except TelegramAPIError:
        await msg.answer("Не вдалося надіслати: клієнт міг заблокувати бота.")
        return
    await actionlog.log(msg.from_user.id, "Порада клієнту", client.name)
    await msg.answer("Надіслано ✅", reply_markup=kb([[btn("👤 До картки", f"acl_{cid}")], [back(MENU)]]))


# ---------- Завдання ----------


async def _tasks_screen(target, edit: bool) -> None:
    async with session_scope() as session:
        rows_db = (await session.execute(select(AdminTask).order_by(AdminTask.done, AdminTask.id))).scalars().all()
    rows = [[btn(("✅ " if t.done else "☐ ") + t.text[:45], f"pro_td_{t.id}")] for t in rows_db[:25]]
    rows.append([btn("➕ Нове завдання", "pro_tnew")])
    if any(t.done for t in rows_db):
        rows.append([btn("🧹 Прибрати виконані", "pro_tclr")])
    rows.append([back(MENU)])
    text = "📋 <b>Мої завдання</b>\nНатисніть на завдання, щоб відмітити виконаним. Невиконані потрапляють в ранкову сводку."
    if edit:
        await safe_edit(target, text, kb(rows))
    else:
        await target.answer(text, reply_markup=kb(rows))


@router.callback_query(F.data == "pro_tasks")
async def tasks_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _tasks_screen(call, True)


@router.callback_query(F.data.startswith("pro_td_"))
async def task_toggle(call: CallbackQuery) -> None:
    tid = parse_cb_int(call.data, "pro_td_")
    async with session_scope() as session:
        t = await session.get(AdminTask, tid) if tid is not None else None
        if t is not None:
            t.done = not t.done
    await call.answer()
    await _tasks_screen(call, True)


@router.callback_query(F.data == "pro_tclr")
async def task_clear(call: CallbackQuery) -> None:
    async with session_scope() as session:
        await session.execute(delete(AdminTask).where(AdminTask.done.is_(True)))
    await call.answer("Прибрано")
    await _tasks_screen(call, True)


@router.callback_query(F.data == "pro_tnew")
async def task_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TaskStates.text)
    await call.answer()
    await safe_edit(call, "Напишіть завдання (наприклад, «купити олію»):", cancel_input_kb("pro_tasks"))


@router.message(TaskStates.text, F.text)
async def task_save(msg: Message, state: FSMContext) -> None:
    async with session_scope() as session:
        session.add(AdminTask(text=msg.text.strip()[:300]))
    await state.clear()
    await _tasks_screen(msg, False)


# ---------- Топ питань клієнтів ----------


def _norm(text: str) -> str:
    return " ".join(text.casefold().replace("?", " ").split())[:70]


@router.callback_query(F.data == "pro_topq")
async def top_questions(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    since = now_local().replace(tzinfo=None) - timedelta(days=90)
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(ClientQuestion).where(ClientQuestion.created_at >= since).order_by(ClientQuestion.id.desc())
            )
        ).scalars().all()
    groups: dict[str, list[ClientQuestion]] = {}
    for q in rows:
        groups.setdefault(_norm(q.text), []).append(q)
    ranked = sorted(groups.values(), key=lambda g: (-len(g), -g[0].id))[:8]
    if not ranked:
        await call.answer()
        await safe_edit(call, "Поки що клієнти не ставили запитань.", kb([[back(MENU)]]))
        return
    lines = ["❓ <b>Про що питають найчастіше</b> (за 3 місяці)\n"]
    buttons = []
    for g in ranked:
        lines.append(f"• {len(g)}× {esc(g[0].text[:100])}")
        buttons.append([btn(f"➕ У FAQ: {g[0].text[:35]}", f"pro_q2f_{g[0].id}")])
    buttons.append([back(MENU)])
    await call.answer()
    await safe_edit(call, "\n".join(lines), kb(buttons))


@router.callback_query(F.data.startswith("pro_q2f_"))
async def q2faq_start(call: CallbackQuery, state: FSMContext) -> None:
    qid = parse_cb_int(call.data, "pro_q2f_")
    async with session_scope() as session:
        q = await session.get(ClientQuestion, qid) if qid is not None else None
    if q is None:
        await call.answer("Запитання не знайдено.", show_alert=True)
        return
    await state.set_state(FaqFromQStates.answer)
    await state.update_data(question=q.text[:200])
    await call.answer()
    await safe_edit(
        call,
        f"Запитання: <b>{esc(q.text[:200])}</b>\n\nНапишіть відповідь, я додам її в «Часті питання». "
        "Клієнти бачитимуть її одразу, ще до того як напишуть вам.",
        cancel_input_kb("pro_topq"),
    )


@router.message(FaqFromQStates.answer, F.text)
async def q2faq_save(msg: Message, state: FSMContext) -> None:
    question = (await state.get_data()).get("question")
    await state.clear()
    if not question:
        return
    async with session_scope() as session:
        session.add(FaqItem(question=question, answer=msg.text.strip()[:1500]))
    await msg.answer("Додано в «Часті питання» ✅", reply_markup=kb([[back(MENU)]]))


# ---------- Ідеї від клієнтів ----------


@router.callback_query(F.data == "pro_sug")
async def sug_list(call: CallbackQuery) -> None:
    async with session_scope() as session:
        sugg = (await session.execute(select(Suggestion).where(Suggestion.hidden.is_(False)))).scalars().all()
        votes = dict(
            (await session.execute(select(SuggestionVote.suggestion_id, func.count()).group_by(SuggestionVote.suggestion_id))).all()
        )
    sugg = sorted(sugg, key=lambda s: -votes.get(s.id, 0))[:15]
    await call.answer()
    if not sugg:
        await safe_edit(call, "💡 Ідей від клієнтів поки немає.", kb([[back(MENU)]]))
        return
    lines = ["💡 <b>Ідеї від клієнтів</b> (за кількістю голосів)\n"]
    rows = []
    for s in sugg:
        lines.append(f"👍 {votes.get(s.id, 0)} · {esc(s.text)}")
        rows.append([btn(f"🙈 Сховати: {s.text[:30]}", f"pro_sugh_{s.id}")])
    rows.append([back(MENU)])
    await safe_edit(call, "\n".join(lines)[:3900], kb(rows))


@router.callback_query(F.data.startswith("pro_sugh_"))
async def sug_hide(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "pro_sugh_")
    async with session_scope() as session:
        s = await session.get(Suggestion, sid) if sid is not None else None
        if s is not None:
            s.hidden = True
    await call.answer("Сховано")
    await sug_list(call)


# ---------- Рівні клієнтів ----------


def _lvl_screen() -> tuple[str, object]:
    cfg = levels.config()
    lines = [
        "🏅 <b>Рівні клієнтів</b>",
        "Статус: " + ("увімкнено" if cfg.get("on", True) else "вимкнено"),
        "Знижка за замовчуванням 0%: рівні лише показуються клієнтам, поки ви самі не вкажете відсоток.\n",
    ]
    rows = [[btn("⏻ Вимкнути" if cfg.get("on", True) else "⏻ Увімкнути", "pro_lvl_on")]]
    for i, t in enumerate(cfg["tiers"]):
        lines.append(f"{i + 1}. <b>{esc(t['name'])}</b>: від {t['min']} візитів, знижка {t['pct']}%")
        row = [btn(f"% «{t['name'][:12]}»", f"pro_lvp_{i}")]
        if i > 0:
            row.append(btn("Візитів", f"pro_lvm_{i}"))
        rows.append(row)
    rows.append([back(MENU)])
    return "\n".join(lines), kb(rows)


@router.callback_query(F.data == "pro_lvl")
async def lvl_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text, markup = _lvl_screen()
    await call.answer()
    await safe_edit(call, text, markup)


@router.callback_query(F.data == "pro_lvl_on")
async def lvl_toggle(call: CallbackQuery) -> None:
    cfg = levels.config()
    cfg["on"] = not cfg.get("on", True)
    await levels.save(cfg)
    await call.answer()
    text, markup = _lvl_screen()
    await safe_edit(call, text, markup)


@router.callback_query(F.data.regexp(r"^pro_lv[pm]_\d+$"))
async def lvl_edit_start(call: CallbackQuery, state: FSMContext) -> None:
    field = "pct" if call.data.startswith("pro_lvp_") else "min"
    idx = int(call.data.split("_")[-1])
    cfg = levels.config()
    if idx >= len(cfg["tiers"]):
        await call.answer()
        return
    await state.set_state(LevelStates.value)
    await state.update_data(idx=idx, field=field)
    await call.answer()
    what = "відсоток знижки (0–50)" if field == "pct" else "скільки візитів потрібно для цього рівня (1–200)"
    await safe_edit(
        call,
        f"Рівень «{esc(cfg['tiers'][idx]['name'])}»: напишіть {what}.",
        cancel_input_kb("pro_lvl"),
    )


@router.message(LevelStates.value, F.text)
async def lvl_edit_save(msg: Message, state: FSMContext) -> None:
    d = await state.get_data()
    raw = msg.text.strip().rstrip("%")
    if not raw.isdigit():
        await msg.answer("Потрібне ціле число.")
        return
    n = int(raw)
    lo, hi = (0, 50) if d.get("field") == "pct" else (1, 200)
    if not lo <= n <= hi:
        await msg.answer(f"Число має бути від {lo} до {hi}.")
        return
    cfg = levels.config()
    if d["idx"] < len(cfg["tiers"]):
        cfg["tiers"][d["idx"]][d["field"]] = n
        await levels.save(cfg)
    await state.clear()
    text, markup = _lvl_screen()
    await msg.answer("Збережено ✅\n\n" + text, reply_markup=markup)


# ---------- Бібліотека «Корисне» ----------


async def _lib_menu(call: CallbackQuery) -> None:
    on = await jobs2.tips_enabled()
    rows = [[btn(title, f"pro_libk_{k}")] for k, title in library.KINDS.items()]
    rows.append(
        [btn("🔕 Вимкнути поради тижня" if on else "🔔 Увімкнути поради тижня (по четвергах)", "pro_tips_toggle")]
    )
    rows.append([back(MENU)])
    await safe_edit(
        call,
        "📚 <b>Бібліотека «Корисне»</b>\nЦе бачать клієнти в розділі «📚 Корисне». Усі тексти можна змінювати.\n"
        f"Поради тижня клієнтам: {'увімкнено' if on else 'вимкнено (перегляньте тексти й увімкніть)'}.",
        kb(rows),
    )


@router.callback_query(F.data == "pro_lib")
async def lib_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _lib_menu(call)


@router.callback_query(F.data == "pro_tips_toggle")
async def lib_tips_toggle(call: CallbackQuery) -> None:
    await jobs2.set_flag("tips_on", not await jobs2.tips_enabled())
    await call.answer()
    await _lib_menu(call)


async def _lib_kind(call: CallbackQuery, kind: str) -> None:
    async with session_scope() as session:
        its = await library.items(session, kind, active_only=False)
    rows = [[btn(("✅ " if it.active else "⏸ ") + it.title[:40], f"pro_libt_{it.id}"), btn("🗑", f"pro_libd_{it.id}")] for it in its[:30]]
    rows.append([btn("➕ Додати", f"pro_liba_{kind}")])
    rows.append([back("pro_lib")])
    await safe_edit(
        call,
        f"{library.KINDS[kind]}\n✅ показується, ⏸ сховано. Натисніть на назву, щоб сховати/показати, 🗑 щоб видалити.",
        kb(rows),
    )


@router.callback_query(F.data.regexp(r"^pro_libk_(tip|ex|term)$"))
async def lib_kind(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _lib_kind(call, call.data.removeprefix("pro_libk_"))


@router.callback_query(F.data.regexp(r"^pro_lib[td]_\d+$"))
async def lib_item_action(call: CallbackQuery) -> None:
    iid = int(call.data.split("_")[-1])
    async with session_scope() as session:
        it = await library.get(session, iid)
        if it is None:
            await call.answer()
            return
        kind = it.kind
        if call.data.startswith("pro_libt_"):
            await library.toggle(session, iid)
        else:
            await library.remove(session, iid)
    await call.answer()
    await _lib_kind(call, kind)


@router.callback_query(F.data.regexp(r"^pro_liba_(tip|ex|term)$"))
async def lib_add_start(call: CallbackQuery, state: FSMContext) -> None:
    kind = call.data.removeprefix("pro_liba_")
    await state.set_state(LibStates.title)
    await state.update_data(kind=kind)
    await call.answer()
    await safe_edit(call, "Напишіть заголовок:", cancel_input_kb(f"pro_libk_{kind}"))


@router.message(LibStates.title, F.text)
async def lib_add_title(msg: Message, state: FSMContext) -> None:
    await state.update_data(title=msg.text.strip()[:150])
    await state.set_state(LibStates.text)
    await msg.answer("Тепер напишіть сам текст:")


@router.message(LibStates.text, F.text)
async def lib_add_text(msg: Message, state: FSMContext) -> None:
    d = await state.get_data()
    await state.clear()
    async with session_scope() as session:
        await library.add(session, d.get("kind", "tip"), d.get("title", "Без назви"), msg.text.strip()[:1500])
    await msg.answer("Додано ✅", reply_markup=kb([[btn("📚 До бібліотеки", "pro_lib")]]))


# ---------- Порожні години → розсилка (лише з підтвердження) ----------


@router.callback_query(F.data == "eh_preview")
async def eh_preview(call: CallbackQuery) -> None:
    windows = await emptyhours.weak_windows()
    await call.answer()
    if not windows:
        await safe_edit(
            call,
            "🔍 Поки що замало даних, щоб знайти порожні години (потрібно хоча б 10 записів за кілька тижнів).",
            kb([[back(MENU)]]),
        )
        return
    text = emptyhours.announcement(windows)
    await safe_edit(
        call,
        "🔍 <b>Текст розсилки клієнтам:</b>\n\n" + esc(text) + "\n\nНадіслати всім клієнтам?",
        kb([[btn("✅ Надіслати всім", "eh_send")], [back(MENU)]]),
    )


@router.callback_query(F.data == "eh_send")
async def eh_send(call: CallbackQuery, client_bot: Bot | None = None) -> None:
    from shared.broadcast import segment_ids, send_many

    windows = await emptyhours.weak_windows()
    if not windows or client_bot is None:
        await call.answer("Нічого надсилати.", show_alert=True)
        return
    await call.answer("Надсилаю…")
    ids = await segment_ids("all")
    ok, fail, skipped = await send_many(client_bot, ids, emptyhours.announcement(windows))
    await safe_edit(
        call,
        f"📣 Надіслано: {ok}. Не доставлено: {fail}. Пропущено через тихі години: {skipped}.",
        kb([[back(MENU)]]),
    )


# ---------- Журнал дій (лише власник) ----------


@router.callback_query(F.data == "pro_log")
async def log_view(call: CallbackQuery, is_super: bool = False) -> None:
    if not is_super:
        await call.answer("Журнал доступний лише власнику.", show_alert=True)
        return
    rows = await actionlog.recent(40)
    await call.answer()
    if not rows:
        await safe_edit(call, "📜 Журнал поки порожній.", kb([[back(MENU)]]))
        return
    tz = now_local().tzinfo
    lines = ["📜 <b>Журнал дій</b> (останні 40)\n"]
    for r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        lines.append(f"{ts.astimezone(tz):%d.%m %H:%M} · {r.actor_id} · {esc(r.action)}" + (f" · {esc(r.details[:60])}" if r.details else ""))
    await safe_edit(call, "\n".join(lines)[:3900], kb([[back(MENU)]]))
