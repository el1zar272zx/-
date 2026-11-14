# admin_bot/states.py
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class RejectStates(StatesGroup):
    reason = State()


class WeekdayHoursStates(StatesGroup):
    hours = State()


class ExceptionHoursStates(StatesGroup):
    hours = State()


class BlockTimeStates(StatesGroup):
    interval = State()


class ServiceEditStates(StatesGroup):
    price = State()
    duration = State()
    name = State()
    description = State()
    sale = State()


class NewServiceStates(StatesGroup):
    name = State()
    duration = State()
    price = State()


class NewCategoryStates(StatesGroup):
    name = State()


class CardEditStates(StatesGroup):
    value = State()


class AddAdminStates(StatesGroup):
    telegram_id = State()


class BroadcastStates(StatesGroup):
    text = State()
    confirm = State()
    when = State()


class ManualBookingStates(StatesGroup):
    name = State()
    phone = State()


class ClientSearchStates(StatesGroup):
    query = State()


class ClientNoteStates(StatesGroup):
    text = State()


class SalonStates(StatesGroup):
    value = State()
    photo = State()
    faq_q = State()
    faq_a = State()


class ClientPhotoStates(StatesGroup):
    photo = State()


class AnswerStates(StatesGroup):
    text = State()


class StopwordStates(StatesGroup):
    text = State()


class VisitNoteStates(StatesGroup):
    text = State()


class RestoreStates(StatesGroup):
    file = State()
    confirm = State()


class CareStates(StatesGroup):
    recall = State()
    followup = State()


class WorkStates(StatesGroup):
    photo = State()


class AftercareStates(StatesGroup):
    photo = State()
    rule = State()
    prep = State()


class QuickStates(StatesGroup):
    text = State()


class TplStates(StatesGroup):
    text = State()


class TagStates(StatesGroup):
    text = State()


class TaskStates(StatesGroup):
    text = State()


class AdviceStates(StatesGroup):
    text = State()


class LevelStates(StatesGroup):
    value = State()


class LibStates(StatesGroup):
    title = State()
    text = State()


class FaqFromQStates(StatesGroup):
    answer = State()


class PostStates(StatesGroup):
    title = State()
    body = State()
    photo = State()
    edit_title = State()
    edit_caption = State()
    brand = State()
    tags = State()


class PlaylistStates(StatesGroup):
    title = State()
    url = State()
