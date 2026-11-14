# client_bot/states.py
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class BookingStates(StatesGroup):
    CHOOSING_CITY = State()
    CHOOSING_CATEGORY = State()
    CHOOSING_SERVICE = State()
    CHOOSING_DATE = State()
    CHOOSING_TIME = State()
    CHOOSING_PAYMENT = State()
    CONFIRMATION = State()
    GUEST_NAME = State()
    GUEST_PHONE = State()
    WISH = State()


class ProfileStates(StatesGroup):
    EDIT_NAME = State()
    EDIT_BDAY = State()
    EDIT_ABOUT = State()


class IntakeStates(StatesGroup):
    CHOOSING = State()


class AskStates(StatesGroup):
    TEXT = State()


class ReviewStates(StatesGroup):
    COMMENT = State()


class SuggestStates(StatesGroup):
    TEXT = State()


class ZoneStates(StatesGroup):
    PHOTO = State()


class SkinPassStates(StatesGroup):
    ALLERGY = State()
    REACT = State()


class LostStates(StatesGroup):
    TEXT = State()


class PhotoDiaryStates(StatesGroup):
    PHOTO = State()
