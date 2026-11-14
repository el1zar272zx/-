# shared/redflags.py — «червоні прапорці» для картки запису: з анкети клієнта й відповідей про протипоказання
from __future__ import annotations

KEYWORDS = (
    ("вагітн", "вагітність"),
    ("груддю", "годування груддю"),
    ("годуван", "годування груддю"),
    ("алерг", "алергія"),
    ("діабет", "діабет"),
    ("онко", "онкологія"),
    ("тромб", "тромбоз"),
    ("варикоз", "варикоз"),
    ("тиск", "підвищений тиск"),
    ("епілеп", "епілепсія"),
    ("кардіостим", "кардіостимулятор"),
    ("герпес", "герпес"),
    ("гепатит", "гепатит"),
    ("шкірні захворювання", "шкірні захворювання"),
    ("розріджув", "розріджувачі крові"),
)


def from_text(text: str | None) -> list[str]:
    low = (text or "").casefold()
    if not low or low.startswith("нічого не вказано"):
        return []
    found: list[str] = []
    for key, label in KEYWORDS:
        if key in low and label not in found:
            found.append(label)
    return found


def collect(client, extra: list[str] | None = None) -> list[str]:
    """Усі прапорці клієнта: з анкети (intake), поля «про мене» й відповідей у записі."""
    flags: list[str] = []
    for src in (getattr(client, "intake", None), getattr(client, "about", None)):
        for f in from_text(src):
            if f not in flags:
                flags.append(f)
    for e in extra or []:
        if e and e not in flags:
            flags.append(e)
    return flags


def line(flags_text: str | None) -> str:
    return f"🚩 Увага: {flags_text}" if flags_text else ""
