# admin_bot/handlers/broadcast.py — розсилки: аудиторії, тест собі, відкладена відправка, ідеї
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from shared import broadcast as bc
from shared.database import session_scope
from shared.models import Service
from shared.tg import Event, ack, respond, safe_edit
from shared.utils import esc, now_local

from ..keyboards import back, btn, cancel_input_kb, kb, menu_only_kb
from ..states import BroadcastStates

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")


async def broadcast_start(event: Event, state: FSMContext) -> None:
    await state.clear()
    rows = []
    for key, label in bc.SEGMENTS.items():
        n = len(await bc.segment_ids(key))
        rows.append([btn(f"{label} ({n})", f"abc_seg_{key}")])
    rows.append([btn("💆 Хто ходив на послугу…", "abc_svcs")])
    pend = await bc.pending()
    if pend:
        rows.append([btn(f"⏰ Заплановані ({len(pend)})", "abc_sched")])
    rows.append([btn("🏠 Меню", "adm_menu")])
    await ack(event)
    await respond(
        event,
        "📣 <b>Розсилка</b>\nОберіть, кому надіслати. Перед відправкою можна переглянути текст, "
        "надіслати його собі для перевірки або запланувати на потрібний час.",
        kb(rows),
    )


@router.callback_query(F.data == "abc_start")
async def abc_start(call: CallbackQuery, state: FSMContext) -> None:
    await broadcast_start(call, state)


@router.callback_query(F.data == "abc_svcs")
async def abc_svcs(call: CallbackQuery) -> None:
    async with session_scope() as session:
        services = (
            await session.execute(select(Service).where(Service.is_active.is_(True)).order_by(Service.name))
        ).scalars().all()
    rows = [[btn(s.name[:40], f"abc_seg_svc:{s.id}")] for s in services[:30]]
    rows.append([back("abc_start")])
    await call.answer()
    await safe_edit(call, "Оберіть послугу: розсилка піде тим, хто на неї ходив.", kb(rows))


@router.callback_query(F.data.regexp(r"^abc_seg_(\w+|svc:\d+)$"))
async def abc_segment(call: CallbackQuery, state: FSMContext) -> None:
    seg = call.data.removeprefix("abc_seg_")
    ids = await bc.segment_ids(seg)
    if not ids:
        await call.answer("У цій групі поки нікого немає.", show_alert=True)
        return
    await state.set_state(BroadcastStates.text)
    await state.update_data(segment=seg)
    await call.answer()
    label = await bc.segment_label(seg)
    await safe_edit(
        call,
        f"📣 Аудиторія: <b>{esc(label)}</b> ({len(ids)}).\n"
        "Напишіть повідомлення одним текстом або візьміть готову ідею.",
        kb([[btn("💡 Ідеї текстів", "abc_ideas")], [back("abc_start")]]),
    )


@router.callback_query(F.data == "abc_ideas")
async def abc_ideas(call: CallbackQuery) -> None:
    rows = [[btn(title, f"abc_idea_{i}")] for i, (title, _) in enumerate(bc.IDEAS)]
    rows.append([back("abc_start")])
    await call.answer()
    await safe_edit(call, "💡 Оберіть заготовку, потім її можна буде підправити перед відправкою.", kb(rows))


async def _preview(target: Message, state: FSMContext, text: str) -> None:
    data = await state.get_data()
    seg = data.get("segment", "all")
    ids = await bc.segment_ids(seg)
    await state.update_data(text=text)
    await state.set_state(BroadcastStates.confirm)
    await target.answer(
        f"Так це побачать клієнти ({esc(await bc.segment_label(seg))}, {len(ids)}):\n\n{esc(text)}",
        reply_markup=kb(
            [
                [btn("✅ Надіслати зараз", "abc_yes")],
                [btn("🧪 Надіслати собі для перевірки", "abc_test")],
                [btn("⏰ Запланувати", "abc_later")],
                [btn("✏️ Змінити текст", "abc_edit")],
                [btn("❌ Скасувати", "adm_menu")],
            ]
        ),
    )


@router.callback_query(F.data.regexp(r"^abc_idea_\d+$"))
async def abc_idea(call: CallbackQuery, state: FSMContext) -> None:
    i = int(call.data.removeprefix("abc_idea_"))
    if not 0 <= i < len(bc.IDEAS):
        await call.answer()
        return
    if (await state.get_data()).get("segment") is None:
        await state.update_data(segment="all")
    await call.answer()
    await _preview(call.message, state, bc.IDEAS[i][1])


@router.message(BroadcastStates.text, F.text)
async def abc_text(msg: Message, state: FSMContext) -> None:
    text = msg.text.strip()
    if len(text) < 3 or len(text) > 1000:
        await msg.answer("Текст має бути від 3 до 1000 символів.")
        return
    await _preview(msg, state, text)


@router.callback_query(BroadcastStates.confirm, F.data == "abc_edit")
async def abc_edit(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.text)
    await call.answer()
    await call.message.answer("Надішліть новий текст повідомлення.", reply_markup=cancel_input_kb("abc_start"))


@router.callback_query(BroadcastStates.confirm, F.data == "abc_test")
async def abc_test(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    text = (await state.get_data()).get("text")
    if not text or client_bot is None:
        await call.answer("Немає тексту або клієнтського бота.", show_alert=True)
        return
    try:
        await client_bot.send_message(call.from_user.id, esc(text), request_timeout=30)
        await call.answer("Надіслала вам у клієнтського бота. Перевірте там 👀", show_alert=True)
    except Exception:
        await call.answer(
            "Не вдалося надіслати. Відкрийте клієнтського бота й натисніть /start, потім повторіть.",
            show_alert=True,
        )


@router.callback_query(BroadcastStates.confirm, F.data == "abc_yes")
async def abc_send(call: CallbackQuery, state: FSMContext, client_bot: Bot | None = None) -> None:
    data = await state.get_data()
    text, seg = data.get("text"), data.get("segment", "all")
    await state.clear()
    if not text or client_bot is None:
        await call.answer("Не вдалося: немає тексту або клієнтського бота.", show_alert=True)
        return
    await call.answer("Надсилаю…")
    ids = await bc.segment_ids(seg)
    ok, fail, skipped = await bc.send_many(client_bot, ids, text)
    note = f"\nНе турбувала (тихі години): {skipped}." if skipped else ""
    await call.message.answer(
        f"Розсилку завершено: доставлено {ok}, не доставлено {fail}.{note}", reply_markup=menu_only_kb()
    )


_WHEN = re.compile(r"^\s*(?:(завтра)|(\d{1,2})[.](\d{1,2})(?:[.](\d{2,4}))?)\s+(\d{1,2})[:.](\d{2})\s*$", re.I)


def parse_when(text: str) -> datetime | None:
    m = _WHEN.match(text.lower())
    if not m:
        return None
    now = now_local().replace(tzinfo=None)
    try:
        if m.group(1):
            day = (now + timedelta(days=1)).date()
        else:
            year = int(m.group(4)) if m.group(4) else now.year
            if year < 100:
                year += 2000
            day = datetime(year, int(m.group(3)), int(m.group(2))).date()
        when = datetime.combine(day, datetime.min.time()).replace(hour=int(m.group(5)), minute=int(m.group(6)))
    except ValueError:
        return None
    if not m.group(1) and not m.group(4) and when < now:
        try:
            when = when.replace(year=when.year + 1)
        except ValueError:
            return None
    return when if when > now else None


@router.callback_query(BroadcastStates.confirm, F.data == "abc_later")
async def abc_later(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BroadcastStates.when)
    await call.answer()
    await call.message.answer(
        "⏰ Коли надіслати? Напишіть дату й час, наприклад <code>20.11 10:00</code> або "
        "<code>завтра 09:30</code>.",
        reply_markup=cancel_input_kb("abc_start"),
    )


@router.message(BroadcastStates.when, F.text)
async def abc_when(msg: Message, state: FSMContext) -> None:
    when = parse_when(msg.text)
    if when is None:
        await msg.answer("Не вдалося розпізнати або час уже минув. Приклад: <code>20.11 10:00</code>")
        return
    data = await state.get_data()
    text, seg = data.get("text"), data.get("segment", "all")
    if not text:
        await state.clear()
        await msg.answer("Текст загублено, почніть заново.", reply_markup=menu_only_kb())
        return
    await bc.schedule(text, seg, when)
    await state.clear()
    await msg.answer(
        f"✅ Заплановано на {when:%d.%m.%Y о %H:%M}. Одержувачів визначу в момент відправки.",
        reply_markup=menu_only_kb(),
    )


@router.callback_query(F.data == "abc_sched")
async def abc_sched(call: CallbackQuery) -> None:
    pend = await bc.pending()
    lines = ["⏰ <b>Заплановані розсилки</b>", ""]
    rows = []
    for p in pend:
        lines.append(f"• {p.run_at:%d.%m %H:%M} · {esc(await bc.segment_label(p.segment))}\n  {esc(p.text[:80])}")
        rows.append([btn(f"🗑 Скасувати {p.run_at:%d.%m %H:%M}", f"abc_del_{p.id}")])
    if not pend:
        lines.append("Нічого не заплановано.")
    rows.append([back("abc_start")])
    await call.answer()
    await safe_edit(call, "\n".join(lines), kb(rows))


@router.callback_query(F.data.regexp(r"^abc_del_\d+$"))
async def abc_del(call: CallbackQuery) -> None:
    await bc.delete(int(call.data.removeprefix("abc_del_")))
    await call.answer("Скасовано")
    await abc_sched(call)
