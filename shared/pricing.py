# shared/pricing.py — акційні ціни (стара ціна закреслена, діє до дати)
from __future__ import annotations

from datetime import date

from .utils import fmt_price, today_local


def sale_active(svc, today: date | None = None) -> bool:
    if svc.sale_price is None or svc.price is None:
        return False
    if svc.sale_until is None:
        return True
    return svc.sale_until >= (today or today_local())


def eff_price(svc) -> int | None:
    return svc.sale_price if sale_active(svc) else svc.price


def price_plain(svc) -> str:
    """Для текстів кнопок (без HTML)."""
    if sale_active(svc):
        return f"🔥 {svc.sale_price} грн (було {svc.price})"
    return fmt_price(svc.price)


def price_html(svc) -> str:
    if sale_active(svc):
        until = f" до {svc.sale_until:%d.%m}" if svc.sale_until else ""
        return f"<s>{svc.price} грн</s> <b>{svc.sale_price} грн</b> 🔥 акція{until}"
    return f"<b>{fmt_price(svc.price)}</b>"
