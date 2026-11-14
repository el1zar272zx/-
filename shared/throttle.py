# shared/throttle.py — антифлуд: не даємо одному користувачу засипати бота запитами
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

WINDOW = 8.0      # секунд
MAX_EVENTS = 10   # подій у вікні
BLOCK = 15.0      # на скільки секунд мовчимо, якщо ліміт перевищено вперше
# щабель за повторні порушення протягом доби: 15 с → 2 хв → 30 хв
LADDER = (15.0, 120.0, 1800.0)
STRIKE_TTL = 86400.0


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._hits: dict[int, deque[float]] = defaultdict(deque)
        self._blocked_until: dict[int, float] = {}
        self._warned_at: dict[int, float] = {}
        self._strikes: dict[int, tuple[int, float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        uid, now = user.id, time.monotonic()
        if now < self._blocked_until.get(uid, 0):
            await self._warn(event, uid, now)
            return None
        hits = self._hits[uid]
        hits.append(now)
        while hits and now - hits[0] > WINDOW:
            hits.popleft()
        if len(hits) > MAX_EVENTS:
            n, ts = self._strikes.get(uid, (0, now))
            if now - ts > STRIKE_TTL:
                n = 0
            n += 1
            self._strikes[uid] = (n, now)
            self._blocked_until[uid] = now + LADDER[min(n, len(LADDER)) - 1]
            hits.clear()
            if n == len(LADDER):
                await self._alert(data, user)
            await self._warn(event, uid, now)
            return None
        if len(self._hits) > 5000:  # не росте безкінечно
            self._hits.clear()
            self._blocked_until.clear()
            self._warned_at.clear()
            self._strikes.clear()
        return await handler(event, data)

    async def _warn(self, event: TelegramObject, uid: int, now: float) -> None:
        try:
            if isinstance(event, CallbackQuery):
                await event.answer("Не так швидко 🙂 Зачекайте кілька секунд.")
            elif isinstance(event, Message) and now - self._warned_at.get(uid, 0) > BLOCK:
                self._warned_at[uid] = now
                await event.answer("Забагато повідомлень поспіль. Зачекайте кілька секунд, будь ласка 🙂")
        except Exception:
            pass

    async def _alert(self, data: dict[str, Any], user: Any) -> None:
        """Тричі за добу перевищив ліміт: тихо повідомляємо власника."""
        try:
            from .alerts import alert_owner

            name = " ".join(x for x in (user.first_name, user.last_name) if x) or "—"
            uname = f"@{user.username}" if user.username else "без username"
            await alert_owner(
                data.get("admin_bot"),
                "Підозра на спам у боті",
                f"Користувач {name} ({uname}, id {user.id}) тричі за добу засипав бота запитами. "
                "Його автоматично притримано на 30 хвилин. Заблокувати запис можна в картці клієнта.",
            )
        except Exception:
            pass
