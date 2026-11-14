# shared/health.py — «здоров'я» бота: сигнал життя, відстеження аварійних перезапусків, звіт /status
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

from .database import engine, session_scope
from . import backup
from .models import Booking, BookingStatus

logger = logging.getLogger(__name__)
LOGS = Path("logs")
HEARTBEAT = LOGS / "heartbeat.txt"
CLEAN = LOGS / "clean_exit"
STARTED = time.time()


def touch_heartbeat() -> None:
    try:
        LOGS.mkdir(exist_ok=True)
        HEARTBEAT.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        logger.warning("Не вдалося записати heartbeat")


def startup_report() -> str:
    """Текст для власника: звичайний запуск чи перезапуск після збою. Також скидає позначку чистого виходу."""
    crashed = HEARTBEAT.exists() and not CLEAN.exists()
    last = ""
    if HEARTBEAT.exists():
        try:
            last = HEARTBEAT.read_text(encoding="utf-8").strip()
        except OSError:
            last = ""
    try:
        CLEAN.unlink(missing_ok=True)
    except OSError:
        pass
    touch_heartbeat()
    if crashed:
        return "⚠️ Бот запущено після аварійної зупинки" + (f" (останній сигнал: {last})" if last else "") + "."
    return "✅ Бот запущено."


def mark_clean_exit() -> None:
    try:
        LOGS.mkdir(exist_ok=True)
        CLEAN.write_text("1", encoding="utf-8")
    except OSError:
        pass


def _recent_errors() -> int:
    log = LOGS / "bot.log"
    if not log.exists():
        return 0
    try:
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-3000:]
    except OSError:
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    return sum(1 for ln in lines if ln.startswith(today) and " ERROR" in ln)


async def status_text() -> str:
    up = int(time.time() - STARTED)
    h, m = up // 3600, up % 3600 // 60
    async with session_scope() as session:
        pending = await session.scalar(
            select(func.count()).select_from(Booking).where(Booking.status == BookingStatus.PENDING)
        )
        total = await session.scalar(select(func.count()).select_from(Booking))
    db = Path(engine.url.database or "")
    size = f"{db.stat().st_size / 1024:.0f} КБ" if db.exists() else "—"
    return (
        "🩺 <b>Стан бота</b>\n"
        f"Працює без перезапуску: {h} год {m} хв\n"
        f"Заявок, що чекають підтвердження: {pending}\n"
        f"Усього записів у базі: {total}\n"
        f"Помилок у журналі за сьогодні: {_recent_errors()}\n"
        f"Розмір бази: {size}\n\n"
        "💾 <b>Резервні копії</b>\n" + backup.status_lines()
    )
