# shared/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)


def _env_bool(*names: str, default: bool = False) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip():
            return raw.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_opt_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _normalize_db_url(url: str) -> str:
    """
    - sqlite:///x.db  ->  sqlite+aiosqlite:///x.db (проєкт асинхронний)
    - відносний шлях до sqlite-файлу рахуємо від кореня проєкту, а не від cwd
    """
    url = url.strip()
    if url.startswith("sqlite:///"):
        url = "sqlite+aiosqlite:///" + url[len("sqlite:///"):]
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        is_abs = path.startswith("/") or (len(path) > 1 and path[1] == ":")
        if path and path != ":memory:" and not is_abs:
            url = prefix + (BASE_DIR / path).as_posix()
    return url


@dataclass
class Settings:
    database_url: str = _normalize_db_url(
        os.getenv("DATABASE_URL", "") or "sqlite+aiosqlite:///./salon.db"
    )
    db_echo: bool = _env_bool("DB_ECHO", "SQLALCHEMY_ECHO", default=False)

    client_bot_token: str = os.getenv("CLIENT_BOT_TOKEN", "").strip()
    admin_bot_token: str = os.getenv("ADMIN_BOT_TOKEN", "").strip()

    superadmin_telegram_id: int | None = _env_opt_int("SUPERADMIN_TELEGRAM_ID")

    timezone: str = os.getenv("TIMEZONE", "").strip() or "Europe/Kyiv"

    # За скільки годин до візиту надсилати нагадування
    notify_hours_before_1: int = _env_int("NOTIFY_HOURS_BEFORE_1", 24)
    notify_hours_before_mid: int = _env_int("NOTIFY_HOURS_BEFORE_MID", 12)
    notify_hours_before_2: int = _env_int("NOTIFY_HOURS_BEFORE_2", 2)
    # Мінімум годин до візиту, за які клієнт ще може перенести запис
    min_change_hours: int = _env_int("MIN_CHANGE_HOURS", 3)
    # За скільки хвилин до візиту попередити адміністратора
    admin_notify_minutes_before: int = _env_int("ADMIN_NOTIFY_MINUTES_BEFORE", 60)

    # Крок слотів (хвилини)
    slot_step_minutes: int = _env_int("SLOT_STEP_MINUTES", 30)

    # На скільки днів уперед можна записатися
    max_days_ahead: int = _env_int("MAX_DAYS_AHEAD", 30)

    # Мінімум хвилин до початку, щоб ще можна було записатися (для "сьогодні")
    min_lead_minutes: int = _env_int("MIN_LEAD_MINUTES", 60)

    # Один майстер на обидва міста: запис в одному місті блокує час в іншому
    single_specialist: bool = _env_bool("SINGLE_SPECIALIST", default=True)

    # Через скільки хвилин після закінчення візиту просити відгук
    review_delay_minutes: int = _env_int("REVIEW_DELAY_MINUTES", 120)

    # Щоденні задачі (година за TIMEZONE)
    morning_digest_hour: int = _env_int("MORNING_DIGEST_HOUR", 8)
    backup_hour: int = _env_int("BACKUP_HOUR", 3)

    # Лояльність: кожен N-й візит зі знижкою P%. Знижка 0 = лише лічильник візитів.
    loyalty_every: int = _env_int("LOYALTY_EVERY", 5)
    loyalty_discount_percent: int = _env_int("LOYALTY_DISCOUNT_PERCENT", 0)

    # Пауза між сеансами (хвилини): час на прибирання й відпочинок
    buffer_minutes: int = _env_int("BUFFER_MINUTES", 10)

    # Інфо про салон (показується кнопкою «Як нас знайти»)
    salon_address: str = os.getenv("SALON_ADDRESS", "").strip()
    salon_map_url: str = os.getenv("SALON_MAP_URL", "").strip()
    salon_phone: str = os.getenv("SALON_PHONE", "").strip()
    salon_lat: str = os.getenv("SALON_LAT", "").strip()
    salon_lon: str = os.getenv("SALON_LON", "").strip()

    # Нагадати «давно не були» через стільки днів після останнього візиту
    reengage_days: int = _env_int("REENGAGE_DAYS", 30)

    # Додаткова папка для копій бази (наприклад, папка Google Drive / OneDrive)
    backup_extra_dir: str = os.getenv("BACKUP_EXTRA_DIR", "").strip()

    scheduler_log: bool = _env_bool("SCHEDULER_LOG", default=True)


settings = Settings()
