# shared/backup.py — копія бази SQLite для надсилання адмінам
from __future__ import annotations

import logging
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

_PREFIX = "sqlite+aiosqlite:///"


def db_file_path() -> Path | None:
    url = settings.database_url
    if not url.startswith(_PREFIX):
        return None
    raw = url[len(_PREFIX):]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def make_backup_copy() -> tuple[Path, str] | None:
    """
    Робить консистентну копію бази (через sqlite backup API — безпечно, поки бот працює).
    Повертає (шлях до тимчасового файлу, імʼя для відправки) або None, якщо база не SQLite.
    """
    src_path = db_file_path()
    if src_path is None or not src_path.exists():
        return None
    name = f"salon_backup_{datetime.now():%Y-%m-%d_%H-%M}.db"
    tmp_dir = Path(tempfile.mkdtemp(prefix="salon_backup_"))
    dst_path = tmp_dir / name
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dst_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dst_path, name


# ---------- Перевірка, хмарна папка, відновлення ----------

import os
import shutil
import zipfile

PENDING_RESTORE = Path("restore_pending.db")
LOCAL_DIR = Path("backups")
KEEP = 14
_REQUIRED = ("clients", "bookings", "services")


def validate_db(path: Path) -> tuple[bool, str, dict]:
    """Відкриває файл як SQLite, перевіряє цілісність і наявність головних таблиць."""
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return False, f"не вдалося відкрити файл ({e})", {}
    try:
        res = con.execute("PRAGMA integrity_check").fetchone()
        if not res or res[0] != "ok":
            return False, f"база пошкоджена: {res[0] if res else '?'}", {}
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = [t for t in _REQUIRED if t not in tables]
        if missing:
            return False, "це не база салону (немає таблиць: " + ", ".join(missing) + ")", {}
        info = {
            "clients": con.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
            "bookings": con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
            "last": con.execute("SELECT MAX(date) FROM bookings").fetchone()[0],
        }
        return True, "ok", info
    except sqlite3.Error as e:
        return False, f"помилка читання ({e})", {}
    finally:
        con.close()


def live_integrity() -> tuple[bool, str]:
    src = db_file_path()
    if src is None or not src.exists():
        return True, "база не є файлом SQLite"
    ok, msg, _ = validate_db(src)
    return ok, msg


def cloud_dir() -> Path | None:
    """Папка для копії «поза комп'ютером»: з .env або автоматично OneDrive."""
    if settings.backup_extra_dir:
        return Path(settings.backup_extra_dir)
    for var in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        base = os.environ.get(var)
        if base and Path(base).is_dir():
            return Path(base) / "massage_salon_backups"
    return None


def local_backups() -> list[Path]:
    if not LOCAL_DIR.exists():
        return []
    return sorted(LOCAL_DIR.glob("salon_backup_*.db"), key=lambda p: p.stat().st_mtime)


def status_lines() -> str:
    files = local_backups()
    cloud = cloud_dir()
    lines = []
    if files:
        last = files[-1]
        age = datetime.now() - datetime.fromtimestamp(last.stat().st_mtime)
        hours = int(age.total_seconds() // 3600)
        lines.append(f"Остання копія: {datetime.fromtimestamp(last.stat().st_mtime):%d.%m %H:%M} ({hours} год тому)")
        lines.append(f"Копій на комп'ютері: {len(files)} (зберігаю останні {KEEP})")
    else:
        lines.append("Копій на комп'ютері поки немає")
    lines.append(
        f"Хмарна папка: {cloud}" if cloud else "Хмарна папка: не знайдена (OneDrive не виявлено)"
    )
    ok, msg = live_integrity()
    lines.append("Перевірка бази: " + ("✅ без помилок" if ok else f"⚠️ {msg}"))
    return "\n".join(lines)


def stage_restore(src: Path) -> None:
    shutil.copy2(src, PENDING_RESTORE)


def apply_pending_restore() -> str | None:
    """Викликається на старті ДО підключення до бази. Повертає текст для журналу або None."""
    if not PENDING_RESTORE.exists():
        return None
    target = db_file_path()
    try:
        ok, msg, info = validate_db(PENDING_RESTORE)
        if not ok or target is None:
            PENDING_RESTORE.unlink(missing_ok=True)
            return f"Відновлення скасовано: {msg}"
        if target.exists():
            LOCAL_DIR.mkdir(exist_ok=True)
            shutil.copy2(target, LOCAL_DIR / f"before_restore_{datetime.now():%Y-%m-%d_%H-%M-%S}.bak")
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        shutil.copy2(PENDING_RESTORE, target)
        PENDING_RESTORE.unlink(missing_ok=True)
        return f"Базу відновлено з копії (клієнтів: {info['clients']}, записів: {info['bookings']})"
    except OSError as e:
        return f"Відновлення не вдалося: {e}"


def extract_db(data: bytes, dest_dir: Path) -> Path | None:
    """З файлу, надісланого в чат (.db або .zip), дістає базу у тимчасову папку."""
    import io

    dest = dest_dir / "incoming.db"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".db")]
                if not names:
                    return None
                dest.write_bytes(zf.read(names[0]))
        except (zipfile.BadZipFile, OSError):
            return None
    elif data[:15] == b"SQLite format 3":
        dest.write_bytes(data)
    else:
        return None
    return dest
