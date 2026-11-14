# shared/clientdata.py — особисті дані клієнта «ключ-значення» (паспорт шкіри, челенджі, курси, прапорці)
from __future__ import annotations

import json
import logging

from sqlalchemy import delete, select

from .database import session_scope
from .models import ClientData

logger = logging.getLogger(__name__)


async def get(tid: int, key: str, default: str = "") -> str:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ClientData).where(ClientData.client_telegram_id == tid, ClientData.key == key)
            )
        ).scalar_one_or_none()
    return default if row is None else row.value


async def put(tid: int, key: str, value: str) -> None:
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ClientData).where(ClientData.client_telegram_id == tid, ClientData.key == key)
            )
        ).scalar_one_or_none()
        if row is None:
            session.add(ClientData(client_telegram_id=tid, key=key, value=value))
        else:
            row.value = value


async def drop(tid: int, key: str) -> None:
    async with session_scope() as session:
        await session.execute(
            delete(ClientData).where(ClientData.client_telegram_id == tid, ClientData.key == key)
        )


async def get_json(tid: int, key: str) -> dict:
    raw = await get(tid, key)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


async def put_json(tid: int, key: str, data: dict) -> None:
    await put(tid, key, json.dumps(data, ensure_ascii=False))


async def rows_for_key(key: str) -> list[tuple[int, str]]:
    """Усі (telegram_id, value) для ключа: для фонових завдань."""
    async with session_scope() as session:
        rows = (
            await session.execute(select(ClientData.client_telegram_id, ClientData.value).where(ClientData.key == key))
        ).all()
    return [(int(t), v) for t, v in rows]


async def passport_lines(tid: int) -> list[str]:
    """Рядки для картки клієнта в адмін-боті: паспорт шкіри, вподобання сеансу, динаміка самопочуття."""
    from . import selfcare_data as sd
    from .utils import esc

    lines: list[str] = []
    try:
        sp = await get_json(tid, "skin_pass")
        parts = []
        if sp.get("type"):
            parts.append("тип: " + sd.skin_label(sp["type"]))
        if sp.get("allergy"):
            parts.append("алергії: " + esc(sp["allergy"]))
        if sp.get("react"):
            parts.append("реакції: " + esc(sp["react"]))
        if parts:
            lines.append("🧴 Паспорт шкіри: " + "; ".join(parts))
        cf = await get_json(tid, "comfort")
        if cf:
            lines.append(
                "🎛 Вподобання сеансу: "
                + ", ".join(sd.comfort_label(k, cf[k]) for k, _q, _o in sd.COMFORT if k in cf)
            )
        from . import wellbeing

        entries = await wellbeing.recent(tid, 20)
        if entries:
            lines.append("💗 Самопочуття: " + wellbeing.summary(entries).replace("\n", " · "))
    except Exception:
        logger.exception("Не вдалося зібрати персональні дані клієнта")
    return lines
