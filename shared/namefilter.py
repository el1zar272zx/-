# shared/namefilter.py — захист від непристойних, образливих і «жартівливих» імен та текстів
# Ловить: укр./рос./англ. лайку, лайку латиницею й транслітом (pizdec, blyat), цифри замість літер (х0й),
# розтягнуті літери (хууууй), склеювання через пробіл («яша лава» = «яшалава») і посилання в імені.
from __future__ import annotations

import re

# Корені, які шукаємо будь-де в «склеєному» рядку (довгі й однозначні)
_ANY = [
    "хуй", "хуе", "хуя", "хуил", "пизд", "бляд", "блят", "блеат", "мудак", "мудил", "мудозв", "залуп",
    "шалав", "шлюх", "пидор", "пидар", "пидрил", "педик", "гандон", "гондон", "дебил", "говн", "дерьм",
    "сучар", "ублюд", "проститут", "дрочил", "дроч", "порно", "долбо", "уебок", "уебан", "заеб",
    "fuck", "shit", "bitch", "cunt", "asshole", "slut", "whore", "nigg", "bastard", "porn", "dickhead",
    "motherfuck", "wanker", "faggot",
]
# Корені, що мають стояти на початку слова (дозволені приставки), щоб не чіпати «Лебанов» тощо
_START = ["ебан", "ебал", "ебат", "ебуч", "еблан", "ебло", "ебаш", "ебну"]
_PREFIX = ("", "у", "за", "по", "на", "вы", "до", "про", "раз", "вз", "под", "от", "съ", "объ", "пере", "при", "с")
# Слова, які вважаються лайкою лише як окреме слово
_TOKEN = {
    "сука", "суки", "сучка", "сучки", "жопа", "жопу", "жопы", "жопи", "член", "чмо", "чмошник", "пися", "писька",
    "лох", "лохи", "гнида", "секс", "траxну", "хер", "пенис", "вагина", "сиськи", "сись",
    "cock", "dick", "pussy", "anal", "sex", "tits", "boobs", "penis", "vagina",
}
# Справжні імена/прізвища, що випадково містять корінь
_ALLOW = {"хуан", "хуліо", "хуліан", "хуерта", "хуертас", "хуссейн", "хусейн"}

_LAT_TO_CYR_LOOK = str.maketrans("abcehkmoptxy", "авсенкмортху")
_LEET = str.maketrans({"0": "о", "3": "з", "6": "б", "@": "а", "$": "с", "1": "и", "!": "и", "4": "ч"})
_LEET2 = str.maketrans({"0": "о", "3": "е", "6": "б", "@": "а", "$": "с", "1": "и", "!": "и", "4": "а"})
_LEET_LAT = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"})
_TRANSLIT_MULTI = [("shch", "щ"), ("sch", "щ"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
                   ("ya", "я"), ("yu", "ю"), ("yo", "е"), ("ye", "е"), ("yi", "и"), ("ph", "ф")]
_TRANSLIT = str.maketrans({"a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "z": "з", "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "r": "р", "s": "с", "t": "т", "u": "у", "f": "ф", "h": "х", "c": "к", "w": "в", "x": "кс", "y": "и", "q": "к"})
_CYR_FIX = str.maketrans({"ё": "е", "і": "и", "ї": "и", "є": "е", "ґ": "г", "ы": "и", "э": "е", "ъ": "", "ь": "", "'": "", "ʼ": "", "’": ""})
_JUNK = re.compile(r"(https?://|t\.me/|www\.|\.com\b|\.ru\b|\.ua\b|@\w)", re.I)
_extra: list[str] = []


def _squash(s: str) -> str:
    s = re.sub(r"[^a-zа-я]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)  # розтягнуті літери: хууууй -> хуй


def _cyr(s: str) -> str:
    return s.translate(_CYR_FIX)


def _variants(text: str) -> list[str]:
    """Різні «читання» тексту: як є, схожі латинські літери, транслітерація, англійська з цифрами."""
    low = _cyr(text.lower())
    out = [_squash(low.translate(_LEET).translate(_LAT_TO_CYR_LOOK)), _squash(low.translate(_LEET2).translate(_LAT_TO_CYR_LOOK))]
    t = low.translate(_LEET_LAT)
    for a, b in _TRANSLIT_MULTI:
        t = t.replace(a, b)
    out.append(_squash(_cyr(t.translate(_TRANSLIT))))
    out.append(_squash(low.translate(_LEET_LAT)))
    return out


def _norm_root(r: str) -> str:
    return _squash(_cyr(r.lower()))


_ANY_N = [_norm_root(r) for r in _ANY]
_START_N = [_norm_root(r) for r in _START]
_TOKEN_N = {_norm_root(t) for t in _TOKEN}
_ALLOW_N = {_norm_root(t) for t in _ALLOW}


def set_extra(words: list[str]) -> None:
    """Додаткові слова, які власник додав у налаштуваннях."""
    global _extra
    _extra = [w for w in (_norm_root(x) for x in words) if len(w) >= 3]


def has_obscene(text: str) -> bool:
    if not text:
        return False
    raw = re.split(r"[\s,.;:/\\|_\-]+", text.lower())
    tokens = [t for t in raw if t]
    # 1) окремі слова: відкидаємо дозволені імена, решту перевіряємо
    kept = []
    for tok in tokens:
        vs = _variants(tok)
        if any(v in _ALLOW_N for v in vs):
            continue
        kept.append(tok)
        for v in vs:
            if v in _TOKEN_N:
                return True
            for root in _START_N:
                for p in _PREFIX:
                    if v.startswith(_cyr(p) + root):
                        return True
    # 2) «склеєний» рядок: ловить «яша лава» → «яшалава»
    for v in _variants(" ".join(kept)):
        if any(root in v for root in _ANY_N) or any(root in v for root in _extra):
            return True
    return False


def check_name(text: str) -> str | None:
    """None, якщо ім'я нормальне; інакше причина: 'obscene' | 'junk' | 'short'."""
    t = " ".join(text.split())
    if not re.search(r"[A-Za-zА-Яа-яЁёІіЇїЄєҐґ]{2}", t):
        return "short"
    if _JUNK.search(t):
        return "junk"
    if has_obscene(t):
        return "obscene"
    return None


MESSAGES = {
    "obscene": "Будь ласка, вкажіть справжнє імʼя без грубих слів 🙏",
    "junk": "У імені не можна залишати посилання чи @username. Напишіть, будь ласка, лише імʼя.",
    "short": "Введіть, будь ласка, коректне імʼя літерами.",
}
TEXT_REJECT = "Будь ласка, без грубих слів 🙏 Сформулюйте інакше."

_strikes: dict[int, int] = {}


def strike(user_id: int) -> int:
    """Рахує порушення від користувача (у памʼяті). Повертає поточну кількість."""
    if len(_strikes) > 2000:
        _strikes.clear()
    _strikes[user_id] = _strikes.get(user_id, 0) + 1
    return _strikes[user_id]


# ---------- Власний список слів власника (зберігається в базі) ----------

_KEY = "stopwords"


async def load_extra() -> list[str]:
    from .database import session_scope
    from .models import SalonInfo

    async with session_scope() as session:
        row = await session.get(SalonInfo, _KEY)
    words = [w.strip() for w in (row.value if row else "").split(",") if w.strip()]
    set_extra(words)
    return words


async def save_extra(words: list[str]) -> None:
    from .database import session_scope
    from .models import SalonInfo

    value = ",".join(dict.fromkeys(w.strip().lower() for w in words if w.strip()))[:1500]
    async with session_scope() as session:
        row = await session.get(SalonInfo, _KEY)
        if row is None:
            session.add(SalonInfo(key=_KEY, value=value))
        else:
            row.value = value
    await load_extra()
