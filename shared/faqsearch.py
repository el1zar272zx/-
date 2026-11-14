# shared/faqsearch.py — пошук відповіді серед часті питання за словами клієнта (укр. і рос.)
from __future__ import annotations

import re

_WORD = re.compile(r"[a-zа-яіїєґʼ'0-9]+", re.IGNORECASE)
STOP = {"який", "яка", "яке", "чи", "це", "що", "як", "для", "мені", "мене", "можна", "треба", "потрібно",
        "какой", "какая", "это", "что", "как", "для", "мне", "можно", "нужно", "если", "когда", "або", "або"}
# російські основи → українські, щоб клієнт міг писати будь-якою
RU_TO_UA = {
    "длит": "триває", "сколько": "скільки", "стоит": "вартіст", "цена": "ціна", "цены": "ціна",
    "адрес": "адрес", "отмен": "скасув", "перенес": "перенос", "перенос": "перенос",
    "боль": "болить", "противопоказ": "протипоказ", "беремен": "вагітн", "оплат": "оплат",
    "карт": "картк", "налич": "готівк", "запис": "запис", "время": "час", "график": "графік",
    "режим": "графік", "работа": "роботи", "рабоч": "робоч", "эффект": "ефект", "результат": "результат",
    "подготов": "підготов", "после": "після", "уход": "догляд", "реабилит": "реабіліт",
}


def _stems(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall(text.lower()):
        if len(w) < 4 or w in STOP:
            continue
        stem = w[:5]
        for ru, ua in RU_TO_UA.items():
            if w.startswith(ru):
                stem = ua[:5]
                break
        out.add(stem)
    return out


def best(query: str, items: list[tuple[int, str, str]], limit: int = 3) -> list[int]:
    """items: (id, питання, відповідь). Повертає id найкращих збігів (порожній список, якщо збігів немає)."""
    q = _stems(query)
    if not q:
        return []
    scored = []
    for fid, question, answer in items:
        qs, ans = _stems(question), _stems(answer)
        score = 2 * len(q & qs) + len(q & ans)
        if score >= 2:
            scored.append((score, fid))
    scored.sort(reverse=True)
    return [fid for _, fid in scored[:limit]]
