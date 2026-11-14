# shared/quickbook.py — швидкий запис текстом: «Анна, масаж, завтра 15:00, +380501234567»
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time, timedelta

from .utils import normalize_phone

WEEKDAYS = {
    "пн": 0, "понеділок": 0, "понеділка": 0, "понедельник": 0,
    "вт": 1, "вівторок": 1, "вівторка": 1, "вторник": 1,
    "ср": 2, "середа": 2, "середу": 2, "среда": 2, "среду": 2,
    "чт": 3, "четвер": 3, "четверг": 3,
    "пт": 4, "п'ятницю": 4, "пʼятниця": 4, "п'ятниця": 4, "пятниця": 4, "пятницю": 4, "пʼятницю": 4, "пятница": 4,
    "сб": 5, "субота": 5, "суботу": 5, "суббота": 5, "субботу": 5,
    "нд": 6, "неділя": 6, "неділю": 6, "воскресенье": 6, "вс": 6,
}
REL = {"сьогодні": 0, "сегодня": 0, "завтра": 1, "післязавтра": 2, "послезавтра": 2}
_PHONE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
_DATE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b|(?:\bо|\bв|\bна)\s+([01]?\d|2[0-3])\b(?!\s*[./])")
_WORD = re.compile(r"[A-Za-zА-Яа-яІіЇїЄєҐґʼ'’\-]+")


@dataclass
class Parsed:
    name: str | None = None
    phone: str | None = None
    service_id: int | None = None
    service_name: str | None = None
    day: date | None = None
    start: time | None = None
    candidates: list[int] | None = None  # кілька підходящих послуг


def _stem(word: str) -> str:
    return word.casefold()[:5]


def parse(text: str, services: list, today: date) -> Parsed:
    """services: об'єкти з id і name. Розпізнає імʼя, телефон, послугу, дату й час."""
    out = Parsed()
    t = text.strip()
    m = _PHONE.search(t)
    if m:
        phone = normalize_phone(m.group(0))
        if phone:
            out.phone = "+" + phone.lstrip("+")
            t = t.replace(m.group(0), " ")
    m = _DATE.search(t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            day = date(year, mo, d)
            if not m.group(3) and day < today:
                day = date(year + 1, mo, d)
            out.day = day
        except ValueError:
            pass
        t = t.replace(m.group(0), " ")
    m = _TIME.search(t)
    if m:
        h = int(m.group(1) if m.group(1) is not None else m.group(3))
        mi = int(m.group(2)) if m.group(2) is not None else 0
        out.start = time(h, mi)
        t = t.replace(m.group(0), " ")
    words = _WORD.findall(t)
    rest: list[str] = []
    for w in words:
        low = w.casefold()
        if low in REL:
            out.day = today + timedelta(days=REL[low])
        elif low in WEEKDAYS:
            delta = (WEEKDAYS[low] - today.weekday()) % 7
            out.day = today + timedelta(days=delta)
        else:
            rest.append(w)
    # послуга: за збігом початків слів
    best: list[tuple[int, int]] = []
    for s in services:
        stems = {_stem(x) for x in _WORD.findall(s.name) if len(x) >= 4}
        score = sum(1 for w in rest if len(w) >= 4 and _stem(w) in stems)
        if score:
            best.append((score, s.id))
    best.sort(reverse=True)
    used: set[str] = set()
    if best:
        top = [sid for sc, sid in best if sc == best[0][0]]
        if len(top) == 1:
            out.service_id = top[0]
            svc = next(s for s in services if s.id == top[0])
            out.service_name = svc.name
        else:
            out.candidates = top
        names = {_stem(x) for sid in top for s in services if s.id == sid for x in _WORD.findall(s.name)}
        used = names
    name_words = [w for w in rest if _stem(w) not in used and len(w) >= 2]
    if name_words:
        out.name = " ".join(name_words[:3]).title()
    return out
