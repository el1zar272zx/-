# admin_bot/handlers/salon.py — «Про салон», фото, питання/відповіді, рекомендації після процедур
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared import salon
from shared.aftercare import add_rule, delete_rule, hours_label, list_rules
from shared.database import session_scope
from shared.services import get_service, update_service
from shared.tg import safe_edit
from shared.works import count_works
from shared.utils import esc, parse_cb_int

from ..keyboards import A_SALON, back, btn, cancel_input_kb, kb, menu_only_kb
from ..states import AftercareStates, SalonStates

logger = logging.getLogger(__name__)
router = Router(name="admin_salon")

LABELS = {"instagram": "посилання на Instagram (наприклад, https://instagram.com/назва). Його побачать клієнти в «Як нас знайти»", "address": "вулицю та номер будинку", "phone": "телефон салону", "map_url": "посилання на карту (Google Maps)", "howto": "як дійти: поверх, код домофона, орієнтир (наприклад: «3 поверх, кв. 12, код 1234, вхід з двору»). Це побачать клієнти в нагадуванні", "pay_url": "посилання для оплати карткою (банка Monobank, LiqPay або інше). Клієнт побачить кнопку «Оплатити онлайн»"}
MAX_PHOTOS = 10


# ---------- Меню «Про салон» ----------


async def _menu_text(is_super: bool = False) -> tuple[str, int, int]:
    async with session_scope() as session:
        photos = await salon.list_photos(session)
        faq = await salon.list_faq(session)
    text = (
        "🏠 <b>Про салон</b>\n\n"
        f"📍 Адреса: {esc(salon.info('address')) or '—'}\n"
        f"📞 Телефон: {esc(salon.info('phone')) or '—'}\n"
        f"🗺 Карта: {esc(salon.info('map_url')) or '—'}\n"
        f"🚪 Як дійти: {esc(salon.info('howto')) or '—'}\n"
        f"💳 Оплата онлайн: {esc(salon.info('pay_url')) or '—'}\n"
        + (f"📷 Instagram: {esc(salon.info('instagram')) or '—'}\n" if is_super else "")
        +
        f"🖼 Фото: {len(photos)} з {MAX_PHOTOS}\n"
        f"❓ Питань і відповідей: {len(faq)}\n\n"
        "Усе це бачать клієнти в розділі «📍 Як нас знайти» та «❓ Часті питання»."
    )
    return text, len(photos), len(faq)


def _menu_kb(is_super: bool = False):
    return kb(
        [
            [btn("📍 Вулиця / адреса", "sal_set_address")],
            [btn("📞 Телефон", "sal_set_phone"), btn("🗺 Карта", "sal_set_map_url")],
            [btn("🖼 Додати фото", "sal_photo"), btn("🗑 Видалити фото", "sal_pdel")],
            [btn("🚪 Як дійти", "sal_set_howto")],
            [btn("💳 Посилання на оплату", "sal_set_pay_url")],
            *([[btn("📷 Instagram", "sal_set_instagram")]] if is_super else []),
            [btn("❓ Питання й відповіді", "sal_faq")],
            [btn("📝 Нотатки до візитів", "vn_list")],
            [btn("🚫 Заборонені слова в іменах", "sw_menu")],
            [btn("🔔 Повторні візити й опитування", "care_menu")],
            [back("adm_menu")],
        ]
    )


@router.message(F.text == A_SALON)
async def bar_salon(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    await salon.load_salon_info()
    text, _, _ = await _menu_text(is_super)
    await msg.answer(text, reply_markup=_menu_kb(is_super), disable_web_page_preview=True)


@router.callback_query(F.data == "sal_menu")
async def cb_salon(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    await state.clear()
    await salon.load_salon_info()
    text, _, _ = await _menu_text(is_super)
    await call.answer()
    await safe_edit(call, text, _menu_kb(is_super))


# ---------- Адреса / телефон / карта ----------


@router.callback_query(F.data.regexp(r"^sal_set_(address|phone|map_url|pay_url|howto|instagram)$"))
async def set_start(call: CallbackQuery, state: FSMContext, is_super: bool = False) -> None:
    key = call.data.removeprefix("sal_set_")
    if key == "instagram" and not is_super:
        await call.answer("Це налаштування доступне лише власнику.", show_alert=True)
        return
    await state.set_state(SalonStates.value)
    await state.update_data(key=key)
    await call.answer()
    await safe_edit(
        call,
        f"Напишіть {LABELS[key]}.\nЩоб стерти значення, надішліть «-».",
        cancel_input_kb("sal_menu"),
    )


@router.message(SalonStates.value, F.text)
async def set_save(msg: Message, state: FSMContext, is_super: bool = False) -> None:
    key = (await state.get_data()).get("key")
    if key not in salon.KEYS or (key == "instagram" and not is_super):
        await state.clear()
        return
    value = "" if msg.text.strip() == "-" else msg.text.strip()[:300]
    await salon.set_info(key, value)
    await state.clear()
    text, _, _ = await _menu_text(is_super)
    await msg.answer("Збережено ✅\n\n" + text, reply_markup=_menu_kb(is_super), disable_web_page_preview=True)


# ---------- Фото ----------


@router.callback_query(F.data == "sal_photo")
async def photo_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SalonStates.photo)
    await call.answer()
    await safe_edit(
        call,
        "Надішліть фото салону (можна кілька, одне за одним). Підпис до фото стане підписом для клієнтів.\n"
        "Коли закінчите, натисніть «Готово».",
        kb([[btn("✅ Готово", "sal_menu")]]),
    )


@router.message(SalonStates.photo, F.photo)
async def photo_save(msg: Message) -> None:
    async with session_scope() as session:
        count = len(await salon.list_photos(session))
        if count >= MAX_PHOTOS:
            await msg.answer(f"Максимум {MAX_PHOTOS} фото. Видаліть зайві.", reply_markup=menu_only_kb())
            return
    buf = await msg.bot.download(msg.photo[-1])
    data = buf.read() if buf else b""
    if not data:
        await msg.answer("Не вдалося завантажити фото. Спробуйте ще раз.")
        return
    async with session_scope() as session:
        await salon.add_photo(session, data, (msg.caption or "")[:200] or None)
    await msg.answer(
        f"Фото додано ({count + 1}). Надішліть ще або натисніть «Готово».",
        reply_markup=kb([[btn("✅ Готово", "sal_menu")]]),
    )


@router.message(SalonStates.photo)
async def photo_wrong(msg: Message) -> None:
    await msg.answer("Надішліть саме фото (не файлом) або натисніть «Готово».")


@router.callback_query(F.data == "sal_pdel")
async def photo_del_list(call: CallbackQuery) -> None:
    async with session_scope() as session:
        photos = await salon.list_photos(session)
    if not photos:
        await call.answer("Фото ще немає.", show_alert=True)
        return
    rows = [
        [btn(f"🗑 Фото {i}" + (f": {p.caption[:25]}" if p.caption else ""), f"sal_pd_{p.id}")]
        for i, p in enumerate(photos, 1)
    ]
    rows.append([back("sal_menu")])
    await call.answer()
    await safe_edit(call, "Яке фото видалити?", kb(rows))


@router.callback_query(F.data.regexp(r"^sal_pd_\d+$"))
async def photo_del(call: CallbackQuery) -> None:
    pid = parse_cb_int(call.data, "sal_pd_")
    async with session_scope() as session:
        await salon.delete_photo(session, pid or 0)
    await call.answer("Видалено")
    await photo_del_list(call)


# ---------- Питання й відповіді ----------


async def _faq_view(call: CallbackQuery) -> None:
    async with session_scope() as session:
        items = await salon.list_faq(session)
    rows = [[btn(f"🗑 {i.question[:40]}", f"sal_fd_{i.id}")] for i in items]
    rows += [[btn("➕ Додати питання", "sal_fa")], [back("sal_menu")]]
    text = "❓ <b>Часті питання</b>\nНатисніть на питання, щоб його видалити.\n\n" + (
        "\n".join(f"• {esc(i.question)}" for i in items) or "Поки порожньо."
    )
    await safe_edit(call, text, kb(rows))


@router.callback_query(F.data == "sal_faq")
async def faq_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer()
    await _faq_view(call)


@router.callback_query(F.data.regexp(r"^sal_fd_\d+$"))
async def faq_delete(call: CallbackQuery) -> None:
    fid = parse_cb_int(call.data, "sal_fd_")
    async with session_scope() as session:
        await salon.delete_faq(session, fid or 0)
    await call.answer("Видалено")
    await _faq_view(call)


@router.callback_query(F.data == "sal_fa")
async def faq_add_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SalonStates.faq_q)
    await call.answer()
    await safe_edit(call, "Напишіть питання (наприклад: «Чи можна з дітьми?»):", cancel_input_kb("sal_faq"))


@router.message(SalonStates.faq_q, F.text)
async def faq_q(msg: Message, state: FSMContext) -> None:
    await state.update_data(q=msg.text.strip()[:200])
    await state.set_state(SalonStates.faq_a)
    await msg.answer("Тепер напишіть відповідь:", reply_markup=cancel_input_kb("sal_faq"))


@router.message(SalonStates.faq_a, F.text)
async def faq_a(msg: Message, state: FSMContext) -> None:
    q = (await state.get_data()).get("q")
    if not q:
        await state.clear()
        return
    async with session_scope() as session:
        await salon.add_faq(session, q, msg.text.strip()[:1500])
    await state.clear()
    await msg.answer("Додано ✅", reply_markup=kb([[btn("❓ До питань", "sal_faq")], [back("adm_menu")]]))


# ---------- Рекомендації після процедури та підготовка ----------


async def _care_text(service_id: int) -> tuple[str, list]:
    async with session_scope() as session:
        svc = await get_service(session, service_id)
        rules = await list_rules(session, service_id)
        works_n = await count_works(session, service_id)
    if svc is None:
        return "Послугу не знайдено.", []
    lines = [
        f"🌿 <b>{esc(svc.name)}</b>",
        "",
        "<b>Після процедури клієнт отримує:</b> перелік «не можна…», а коли мине час, "
        "повідомлення «тепер вже можна».",
    ]
    lines += ["", "<b>Обмеження:</b>"]
    lines += [f"• {esc(r.text)} — {hours_label(r.hours)}" for r in rules] or ["Немає."]
    lines += ["", f"<b>Підготовка (в нагадуванні за добу):</b> {esc(svc.prep) if svc.prep else 'не вказано'}"]
    lines += ["", "ℹ️ Готові шаблони — лише загальні орієнтири. Змініть їх під свій протокол."]
    rows = [[btn(f"🗑 {r.text[:30]} · {hours_label(r.hours)}", f"acare_del_{r.id}")] for r in rules]
    rows += [
        [btn("🖼 " + ("Змінити фото послуги" if svc.photo else "Додати фото послуги"), f"sph_{service_id}")],
    ]
    if svc.photo:
        rows.append([btn("🗑 Прибрати фото", f"sphd_{service_id}")])
    rows.append([btn(f"📸 Фото робіт ({works_n})", f"wrk_{service_id}")])
    rows += [
        [btn("➕ Додати обмеження", f"acare_add_{service_id}")],
        [btn("🧴 Змінити підготовку", f"acare_prep_{service_id}")],
        [back(f"asv_svc_{service_id}")],
    ]
    return "\n".join(lines), rows


@router.callback_query(F.data.regexp(r"^acare_\d+$"))
async def care_open(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    sid = parse_cb_int(call.data, "acare_")
    text, rows = await _care_text(sid or 0)
    await call.answer()
    await safe_edit(call, text, kb(rows) if rows else menu_only_kb())


@router.callback_query(F.data.regexp(r"^acare_del_\d+$"))
async def care_del(call: CallbackQuery) -> None:
    async with session_scope() as session:
        sid = await delete_rule(session, parse_cb_int(call.data, "acare_del_") or 0)
    await call.answer("Видалено")
    if sid:
        text, rows = await _care_text(sid)
        await safe_edit(call, text, kb(rows))


@router.callback_query(F.data.regexp(r"^acare_add_\d+$"))
async def care_add_start(call: CallbackQuery, state: FSMContext) -> None:
    sid = parse_cb_int(call.data, "acare_add_")
    await state.set_state(AftercareStates.rule)
    await state.update_data(service_id=sid)
    await call.answer()
    await safe_edit(
        call,
        "Напишіть обмеження і кількість годин через риску «|».\n"
        "Приклад: <code>вживати алкоголь | 24</code>\n"
        "Приклад: <code>напружувати губи | 48</code>",
        cancel_input_kb(f"acare_{sid}"),
    )


@router.message(AftercareStates.rule, F.text)
async def care_add_save(msg: Message, state: FSMContext) -> None:
    sid = (await state.get_data()).get("service_id")
    if "|" not in msg.text:
        await msg.answer("Потрібен формат: <code>що не можна | години</code>, наприклад «вживати алкоголь | 24».")
        return
    text, _, hours = msg.text.rpartition("|")
    try:
        h = int(hours.strip())
        if not 1 <= h <= 24 * 60:
            raise ValueError
    except ValueError:
        await msg.answer("Кількість годин має бути числом, наприклад 24.")
        return
    text = text.strip()
    if not text:
        await msg.answer("Напишіть, що саме не можна.")
        return
    async with session_scope() as session:
        await add_rule(session, sid, text, h)
    await state.clear()
    body, rows = await _care_text(sid)
    await msg.answer("Додано ✅\n\n" + body, reply_markup=kb(rows))


@router.callback_query(F.data.regexp(r"^acare_prep_\d+$"))
async def prep_start(call: CallbackQuery, state: FSMContext) -> None:
    sid = parse_cb_int(call.data, "acare_prep_")
    await state.set_state(AftercareStates.prep)
    await state.update_data(service_id=sid)
    await call.answer()
    await safe_edit(
        call,
        "Напишіть, як підготуватися до процедури. Це побачить клієнт у нагадуванні за добу.\n"
        "Щоб стерти, надішліть «-».",
        cancel_input_kb(f"acare_{sid}"),
    )


@router.message(AftercareStates.prep, F.text)
async def prep_save(msg: Message, state: FSMContext) -> None:
    sid = (await state.get_data()).get("service_id")
    text = msg.text.strip()
    async with session_scope() as session:
        await update_service(
            session, sid, description=None, set_description=False
        )
        svc = await get_service(session, sid)
        if svc is not None:
            svc.prep = None if text == "-" else text[:600]
    await state.clear()
    body, rows = await _care_text(sid)
    await msg.answer("Збережено ✅\n\n" + body, reply_markup=kb(rows))


# ---------- Фото послуги (його бачать клієнти в каталозі) ----------


@router.callback_query(F.data.regexp(r"^sph_\d+$"))
async def sphoto_ask(call: CallbackQuery, state: FSMContext) -> None:
    sid = parse_cb_int(call.data, "sph_")
    await state.set_state(AftercareStates.photo)
    await state.update_data(service_id=sid)
    await call.answer()
    await safe_edit(
        call,
        "Надішліть фото послуги (як фото, не файлом). Клієнти побачать його в каталозі.",
        cancel_input_kb(f"acare_{sid}"),
    )


@router.message(AftercareStates.photo, F.photo)
async def sphoto_save(msg: Message, state: FSMContext) -> None:
    sid = (await state.get_data()).get("service_id")
    buf = await msg.bot.download(msg.photo[-1])
    data = buf.read() if buf else b""
    if not data or sid is None:
        await msg.answer("Не вдалося завантажити фото. Спробуйте ще раз.")
        return
    async with session_scope() as session:
        svc = await get_service(session, sid)
        if svc is None:
            await state.clear()
            await msg.answer("Послугу не знайдено.")
            return
        svc.photo = data
    await state.clear()
    text, rows = await _care_text(sid)
    await msg.answer("Фото збережено ✅\n\n" + text, reply_markup=kb(rows))


@router.message(AftercareStates.photo)
async def sphoto_wrong(msg: Message) -> None:
    await msg.answer("Надішліть саме фото (не файлом).")


@router.callback_query(F.data.regexp(r"^sphd_\d+$"))
async def sphoto_del(call: CallbackQuery) -> None:
    sid = parse_cb_int(call.data, "sphd_")
    async with session_scope() as session:
        svc = await get_service(session, sid or 0)
        if svc is not None:
            svc.photo = None
    await call.answer("Фото прибрано")
    text, rows = await _care_text(sid or 0)
    await safe_edit(call, text, kb(rows))
