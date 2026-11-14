# shared/levels.py — рівні постійних клієнтів. Знижка за замовчуванням 0%: майстер вмикає її сама в адмінці.
from __future__ import annotations

import json
import logging

from .database import session_scope
from .models import SalonInfo

logger = logging.getLogger(__name__)

KEY = "levels_cfg"
DEFAULT = {
    "on": True,
    "tiers": [
        {"name": "Новачок", "min": 0, "pct": 0},
        {"name": "Постійний клієнт", "min": 3, "pct": 0},
        {"name": "VIP", "min": 10, "pct": 0},
    ],
}
_cfg: dict = json.loads(json.dumps(DEFAULT))


async def load() -> None:
    global _cfg
    try:
        async with session_scope() as session:
            row = await session.get(SalonInfo, KEY)
        if row and row.value:
            data = json.loads(row.value)
            if isinstance(data.get("tiers"), list) and data["tiers"]:
                _cfg = data
    except Exception:
        logger.exception("Не вдалося завантажити рівні, лишаю типові")


async def save(cfg: dict) -> None:
    global _cfg
    cfg["tiers"] = sorted(cfg["tiers"], key=lambda t: t["min"])
    async with session_scope() as session:
        row = await session.get(SalonInfo, KEY)
        text = json.dumps(cfg, ensure_ascii=False)
        if row is None:
            session.add(SalonInfo(key=KEY, value=text))
        else:
            row.value = text
    _cfg = cfg


def config() -> dict:
    return json.loads(json.dumps(_cfg))


def enabled() -> bool:
    return bool(_cfg.get("on", True))


def tier_for(visits: int) -> dict:
    cur = _cfg["tiers"][0]
    for t in _cfg["tiers"]:
        if visits >= t["min"]:
            cur = t
    return cur


def pct_for(visits: int) -> int:
    """Знижка рівня у відсотках (0, якщо рівні вимкнені або знижку ще не задано)."""
    if not enabled():
        return 0
    try:
        return max(0, min(50, int(tier_for(visits).get("pct", 0))))
    except (TypeError, ValueError):
        return 0


def progress_text(visits: int) -> str:
    """Рядок для профілю: рівень і скільки візитів до наступного."""
    if not enabled():
        return ""
    cur = tier_for(visits)
    nxt = next((t for t in _cfg["tiers"] if t["min"] > visits), None)
    lines = [f"🏅 Ваш рівень: <b>{cur['name']}</b> (візитів: {visits})"]
    if cur.get("pct"):
        lines.append(f"Ваша знижка: {cur['pct']}%")
    if nxt:
        need = nxt["min"] - visits
        word = "візит" if need == 1 else ("візити" if 2 <= need <= 4 else "візитів")
        lines.append(f"До рівня «{nxt['name']}» залишилось {need} {word}.")
    return "\n".join(lines)
