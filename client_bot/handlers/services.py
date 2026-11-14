# client_bot/handlers/services.py — перегляд послуг без запису
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.database import session_scope
from shared.services import (
    get_active_cities,
    get_categories,
    get_service,
    get_services_by_category,
)
from shared.tg import Event, ack, respond, safe_edit
from shared.pricing import price_html
from shared.utils import esc, fmt_price, parse_cb_int
from shared.works import count_works, list_works

from ..keyboards import (
    T,
    BTN_SERVICES,
    cities_kb,
    service_details_kb,
    services_categories_view_kb,
    services_view_list_kb,
    works_after_kb,
)
from ..states import BookingStates
from .booking import _show_cart

logger = logging.getLogger(__name__)
router = Router(name="client_services")


async def _show_service_categories(event: Event) -> None:
    async with session_scope() as session:
        categories = await get_categories(session)
    await ack(event)
    if not categories:
        await respond(event, "Послуги тимчасово недоступні.")
        return
    await respond(
        event, "Оберіть категорію послуг:", services_categories_view_kb(categories)
    )


@router.message(F.text.in_(T(BTN_SERVICES)))
async def menu_services_text(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_service_categories(msg)


@router.callback_query(F.data == "menu_services")
async def menu_services(call: CallbackQuery) -> None:
    await _show_service_categories(call)


@router.callback_query(F.data.startswith("view_cat_"))
async def services_by_category(call: CallbackQuery) -> None:
    cat_id = parse_cb_int(call.data, "view_cat_")
    if cat_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        services = await get_services_by_category(session, cat_id)
    if not services:
        await call.answer("У цій категорії поки немає активних послуг.", show_alert=True)
        return
    await call.answer()
    await safe_edit(call, "Послуги в категорії:", services_view_list_kb(services))


@router.callback_query(F.data.startswith("view_service_"))
async def service_details(call: CallbackQuery) -> None:
    service_id = parse_cb_int(call.data, "view_service_")
    if service_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        service = await get_service(session, service_id)
        works_n = await count_works(session, service_id) if service else 0
        from shared.models import Favorite
        from sqlalchemy import select as _select

        is_fav = bool(
            service
            and (
                await session.execute(
                    _select(Favorite.id).where(
                        Favorite.client_telegram_id == call.from_user.id, Favorite.service_id == service_id
                    )
                )
            ).first()
        )
    if not service or not service.is_active:
        await call.answer("Послугу не знайдено.", show_alert=True)
        return

    text = (
        f"✨ <b>{esc(service.name)}</b>\n\n"
        f"⏱ Тривалість: {service.duration_minutes} хв\n"
        f"💰 Вартість: {price_html(service)}"
    )
    if service.description:
        text += f"\n\n{esc(service.description)}"
    await call.answer()
    markup = service_details_kb(service.id, service.category_id, works_n, fav=is_fav)
    if service.photo:
        from aiogram.types import BufferedInputFile

        caption = text if len(text) <= 1000 else text[:997] + "…"
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.bot.send_photo(
            call.from_user.id,
            BufferedInputFile(service.photo, filename="service.jpg"),
            caption=caption,
            reply_markup=markup,
        )
        return
    await safe_edit(call, text, markup)


@router.callback_query(F.data.startswith("view_works_"))
async def service_works(call: CallbackQuery) -> None:
    """Альбом «Приклади робіт» для послуги."""
    from aiogram.types import BufferedInputFile, InputMediaPhoto

    service_id = parse_cb_int(call.data, "view_works_")
    if service_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        service = await get_service(session, service_id)
        works = await list_works(session, service_id)
    if not service or not service.is_active or not works:
        await call.answer("Фото поки немає.", show_alert=True)
        return
    await call.answer()
    chat_id = call.from_user.id
    try:
        if len(works) == 1:
            await call.bot.send_photo(
                chat_id, BufferedInputFile(works[0].data, "work.jpg"), caption=works[0].caption or None
            )
        else:
            media = [
                InputMediaPhoto(media=BufferedInputFile(w.data, f"work{i}.jpg"), caption=w.caption or None)
                for i, w in enumerate(works[:10])
            ]
            await call.bot.send_media_group(chat_id, media)
    except Exception:
        logger.exception("Не вдалося надіслати фото робіт")
        await call.bot.send_message(chat_id, "Не вдалося завантажити фото. Спробуйте ще раз трохи пізніше.")
        return
    await call.bot.send_message(
        chat_id,
        f"📸 Приклади робіт: <b>{esc(service.name)}</b>",
        reply_markup=works_after_kb(service.id),
    )


@router.callback_query(F.data.startswith("view_book_"))
async def book_from_service(call: CallbackQuery, state: FSMContext) -> None:
    """Запис на обрану в каталозі послугу: далі можна додати ще, потім дата й час."""
    service_id = parse_cb_int(call.data, "view_book_")
    if service_id is None:
        await call.answer()
        return
    async with session_scope() as session:
        service = await get_service(session, service_id)
        cities = await get_active_cities(session)
    if not service or not service.is_active or not cities:
        await call.answer("Запис на цю послугу зараз недоступний.", show_alert=True)
        return
    await state.clear()
    await state.update_data(service_ids=[service.id], category_id=service.category_id)
    if len(cities) == 1:
        await state.update_data(city_id=cities[0].id)
        await _show_cart(call, state)
        return
    await state.set_state(BookingStates.CHOOSING_CITY)
    await call.answer()
    await safe_edit(call, "Оберіть місто:", cities_kb(cities))
