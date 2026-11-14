# shared/tg_session.py — надійна HTTP-сесія до Telegram
# Малі повідомлення йшли нормально, а завантаження файлів/картинок зависало.
# Типове лікування: лише IPv4 і нове зʼєднання на кожен запит (без «завислих» keep-alive).
from __future__ import annotations

import logging
import socket

from aiogram.client.session.aiohttp import AiohttpSession

logger = logging.getLogger(__name__)


def make_session() -> AiohttpSession | None:
    try:
        session = AiohttpSession(timeout=60)
        init = getattr(session, "_connector_init", None)
        if isinstance(init, dict):
            init["family"] = socket.AF_INET
            init["force_close"] = True
            init.pop("ttl_dns_cache", None)
            init["ttl_dns_cache"] = 300
        return session
    except Exception:
        logger.exception("Не вдалося налаштувати HTTP-сесію, беру стандартну")
        return None
