# shared/database.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, echo=settings.db_echo)

if engine.dialect.name == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_enable_fk(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")  # швидше запис, а цілісність лишається
        cursor.execute("PRAGMA busy_timeout=8000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA cache_size=-16000")  # ~16 МБ кешу
        cursor.close()


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Одна транзакція: commit при успіху, rollback при помилці."""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _migrate_clients(conn, insp) -> None:
    if "clients" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("clients")}
    wanted = {
        "notes": "TEXT",
        "birthday": "DATE",
        "lang": "VARCHAR(2) NOT NULL DEFAULT 'uk'",
        "intake": "TEXT",
        "last_reengage_on": "DATE",
        "birthday_greeted_year": "INTEGER",
        "contact_pref": "VARCHAR(8) NOT NULL DEFAULT 'tg'",
        "pref_days": "VARCHAR(20)",
        "booking_blocked": "BOOLEAN NOT NULL DEFAULT 0",
        "photo_consent": "BOOLEAN NOT NULL DEFAULT 0",
        "quiet": "VARCHAR(8)",
        "about": "TEXT",
        "tags": "TEXT",
        "simple": "BOOLEAN NOT NULL DEFAULT 0",
        "tips_off": "BOOLEAN NOT NULL DEFAULT 0",
    }
    for name, ddl in wanted.items():
        if name not in cols:
            conn.exec_driver_sql(f"ALTER TABLE clients ADD COLUMN {name} {ddl}")


def _migrate_sync(conn) -> None:
    """Легка міграція SQLite: додає нові стовпці до вже наявних таблиць."""
    from sqlalchemy import inspect

    insp = inspect(conn)
    _migrate_clients(conn, insp)
    if "services" in insp.get_table_names():
        scols = {c["name"] for c in insp.get_columns("services")}
        if "prep" not in scols:
            conn.exec_driver_sql("ALTER TABLE services ADD COLUMN prep TEXT")
        if "photo" not in scols:
            conn.exec_driver_sql("ALTER TABLE services ADD COLUMN photo BLOB")
        for scol in ("recall_days", "followup_days", "sale_price"):
            if scol not in scols:
                conn.exec_driver_sql(f"ALTER TABLE services ADD COLUMN {scol} INTEGER")
        if "sale_until" not in scols:
            conn.exec_driver_sql("ALTER TABLE services ADD COLUMN sale_until DATE")
        if "contra" not in scols:
            conn.exec_driver_sql("ALTER TABLE services ADD COLUMN contra TEXT")
        if "consent" not in scols:
            conn.exec_driver_sql("ALTER TABLE services ADD COLUMN consent BOOLEAN NOT NULL DEFAULT 0")
    if "bookings" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("bookings")}
    if "visit_note" not in cols:
        conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN visit_note TEXT")
    if "note_done" not in cols:
        conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN note_done BOOLEAN NOT NULL DEFAULT 0")
        # старі візити не засмічують список «без нотаток»
        conn.exec_driver_sql("UPDATE bookings SET note_done = 1")
    if "flags" not in cols:
        conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN flags TEXT")
    if "wish" not in cols:
        conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN wish TEXT")
    if "service_title" not in cols:
        conn.exec_driver_sql("ALTER TABLE bookings ADD COLUMN service_title VARCHAR(400)")
    for col in ("is_notified_12h", "is_admin_notified_soon", "client_confirmed", "is_paid", "aftercare_scheduled", "recall_sent", "followup_sent"):
        if col not in cols:
            conn.exec_driver_sql(
                f"ALTER TABLE bookings ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT 0"
            )
    if "is_review_requested" not in cols:
        conn.exec_driver_sql(
            "ALTER TABLE bookings ADD COLUMN is_review_requested BOOLEAN NOT NULL DEFAULT 0"
        )


def _ensure_indexes(conn) -> None:
    """Додаткові індекси для швидких списків і пошуку (створюються лише якщо їх ще немає)."""
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_bookings_status_date ON bookings (status, date)",
        "CREATE INDEX IF NOT EXISTS ix_bookings_client_date ON bookings (client_telegram_id, date)",
        "CREATE INDEX IF NOT EXISTS ix_clients_phone ON clients (phone)",
        "CREATE INDEX IF NOT EXISTS ix_clients_name ON clients (name)",
    ):
        try:
            conn.exec_driver_sql(ddl)
        except Exception:
            pass


async def init_db() -> None:
    """Створює таблиці, яких ще немає, і дописує нові стовпці у старих."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sync)
        await conn.run_sync(_ensure_indexes)
