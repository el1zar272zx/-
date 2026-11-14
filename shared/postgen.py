# shared/postgen.py — генератор постів для Instagram: картинка (Pillow) і підпис
from __future__ import annotations

import io
import re

from .schedule_image import _BOLD_CANDIDATES, _FONT_CANDIDATES, _font

# палітра: (верх, низ, колір тексту, акцент, назва)
PALETTES: dict[str, tuple[str, str, str, str, str]] = {
    "sage": ("#0f7b5f", "#6fbf9f", "#ffffff", "#f4c95d", "Зелена"),
    "rose": ("#a83c5f", "#f0a9bd", "#ffffff", "#ffe8a3", "Рожева"),
    "night": ("#1b2340", "#566a9c", "#ffffff", "#f6d58e", "Нічна синя"),
    "peach": ("#f4b995", "#fde9db", "#3b2418", "#b4532a", "Персикова"),
    "mint": ("#d8f0e6", "#f7fcfa", "#14231e", "#0f7b5f", "М'ятна світла"),
}
PALETTE_ORDER = list(PALETTES)

SIZES: dict[str, tuple[int, int, str]] = {
    "post": (1080, 1350, "Пост 4:5"),
    "square": (1080, 1080, "Квадрат 1:1"),
    "story": (1080, 1920, "Сторіс 9:16"),
}
SIZE_ORDER = list(SIZES)

BADGES = ["", "АКЦІЯ", "НОВИНКА", "ВІЛЬНІ ВІКНА", "ПОРАДА", "ДЯКУЄМО"]
DIM_ALPHA = [0, 80, 135, 185]  # затемнення фото

_EMOJI = re.compile("[\U00010000-\U0010ffff☀-➿⬀-⯿️‍⌀-⏿]")


def clean(text: str) -> str:
    """У картинці немає емодзі (шрифт їх не малює), прибираємо й зайві пробіли."""
    return re.sub(r"[ \t]+", " ", _EMOJI.sub("", text or "")).strip()


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _wrap(d, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for w in para.split():
            t = (cur + " " + w).strip()
            if d.textlength(t, font=font) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return [l for l in lines if l is not None]


def _try_fit(d, text: str, candidates, size: int, max_w: int, max_lines: int):
    """Текст цілком у заданому розмірі шрифту або None."""
    f = _font(candidates, size)
    lines = _wrap(d, text, f, max_w)
    if len(lines) <= max_lines and all(d.textlength(l, font=f) <= max_w for l in lines):
        return f, lines
    return None


def _fit_block(d, text: str, candidates, start: int, min_size: int, max_w: int, max_lines: int):
    size = start
    while size >= min_size:
        f = _font(candidates, size)
        lines = _wrap(d, text, f, max_w)
        if len(lines) <= max_lines and all(d.textlength(l, font=f) <= max_w for l in lines):
            return f, lines, size
        size -= 4
    f = _font(candidates, min_size)
    lines = _wrap(d, text, f, max_w)[:max_lines]
    if lines:
        while lines[-1] and d.textlength(lines[-1] + "…", font=f) > max_w:
            lines[-1] = lines[-1][:-1]
        if len(_wrap(d, text, f, max_w)) > max_lines:
            lines[-1] += "…"
    return f, lines, min_size


def _background(Image, ImageDraw, ImageFilter, W: int, H: int, pal: tuple, bg: bytes | None, dim: int):
    if bg:
        try:
            src = Image.open(io.BytesIO(bg)).convert("RGB")
            scale = max(W / src.width, H / src.height)
            src = src.resize((max(W, int(src.width * scale)), max(H, int(src.height * scale))), Image.LANCZOS)
            left, top = (src.width - W) // 2, (src.height - H) // 2
            img = src.crop((left, top, left + W, top + H))
            shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shade)
            base = DIM_ALPHA[max(0, min(dim, len(DIM_ALPHA) - 1))]
            for y in range(H):
                t = y / (H - 1)
                a = int(base + (255 - base) * 0.40 * (t ** 1.6))  # знизу темніше: читається підпис
                sd.line([(0, y), (W, y)], fill=(8, 12, 12, min(235, a)))
            return Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB"), True
        except Exception:
            pass
    top_c, bot_c = _hex(pal[0]), _hex(pal[1])
    img = Image.new("RGB", (W, H), pal[0])
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(top_c[i] + (bot_c[i] - top_c[i]) * t) for i in range(3)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 520, -260, W + 220, 480], fill=(255, 255, 255, 40))
    gd.ellipse([-260, H - 520, 420, H + 200], fill=(255, 255, 255, 30))
    return Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), False


def render(
    *,
    title: str,
    sub: str = "",
    price: str = "",
    badge: str = "",
    brand: str = "",
    contact: str = "",
    cta: str = "",
    palette: str = "sage",
    size: str = "post",
    dim: int = 2,
    bg: bytes | None = None,
) -> bytes:
    """Готова картинка поста (JPEG)."""
    from PIL import Image, ImageDraw, ImageFilter

    pal = PALETTES.get(palette, PALETTES["sage"])
    W, H, _ = SIZES.get(size, SIZES["post"])
    img, photo = _background(Image, ImageDraw, ImageFilter, W, H, pal, bg, dim)
    d = ImageDraw.Draw(img)
    ink = "#ffffff" if photo else pal[2]
    accent = pal[3]
    m = 84
    maxw = W - 2 * m
    title, sub, price, badge = clean(title), clean(sub), clean(price), clean(badge)
    brand, contact, cta = clean(brand), clean(contact), clean(cta)

    badge_bottom = m
    if badge:
        f_b = _font(_BOLD_CANDIDATES, 34)
        tw = d.textlength(badge, font=f_b)
        d.rounded_rectangle([m, m, m + tw + 56, m + 68], radius=34, fill=accent)
        txt_c = "#14231e" if sum(_hex(accent)) > 380 else "#ffffff"
        d.text((m + 28, m + 14), badge, font=f_b, fill=txt_c)
        badge_bottom = m + 68

    lines_bottom = [l for l in (cta, contact) if l]
    footer_h = (64 if brand else 0) + 44 * len(lines_bottom) + (30 if (brand or lines_bottom) else 0)
    footer_top = H - m - footer_h
    body_top = max(badge_bottom + 50, int(H * 0.22))
    avail = footer_top - 30 - body_top

    f_p = _font(_BOLD_CANDIDATES, 52)
    price_h = 96 + 22 if price else 0
    chosen = None
    for t_start in (128, 112, 100, 88, 76, 64, 54, 46):
        fit_t = _try_fit(d, title, _BOLD_CANDIDATES, t_start, maxw, 3 if t_start >= 76 else 5)
        if fit_t is None:
            continue
        f_t, t_lines = fit_t
        t_size = t_start
        t_h = int(len(t_lines) * t_size * 1.16) + 28
        for s_start in (46, 42, 38, 34, 30, 27):
            if not sub:
                f_s, s_lines, s_size, s_h = None, [], 0, 0
            else:
                fit_s = _try_fit(d, sub, _FONT_CANDIDATES, s_start, maxw, 12)
                if fit_s is None:
                    continue
                f_s, s_lines = fit_s
                s_size = s_start
                s_h = int(len(s_lines) * s_size * 1.38) + 22
            if t_h + s_h + price_h <= avail:
                chosen = (f_t, t_lines, t_size, t_h, f_s, s_lines, s_size, s_h)
                break
            if not sub:
                break
        if chosen:
            break
    if chosen is None:  # дуже довгий текст: найменші шрифти й обрізання
        f_t, t_lines, t_size = _fit_block(d, title, _BOLD_CANDIDATES, 46, 46, maxw, 4)
        f_s, s_lines, s_size = (None, [], 0)
        if sub:
            f_s, s_lines, s_size = _fit_block(d, sub, _FONT_CANDIDATES, 27, 27, maxw, max(2, int((avail - 4 * 54 - price_h - 80) / 37)))
        chosen = (f_t, t_lines, t_size, int(len(t_lines) * t_size * 1.16) + 28, f_s, s_lines, s_size,
                  int(len(s_lines) * s_size * 1.38) + 22 if sub else 0)
    f_t, t_lines, t_size, t_h, f_s, s_lines, s_size, s_h = chosen
    y = body_top + max(0, min((avail - (t_h + s_h + price_h)) // 3, 140))
    for line in t_lines:
        d.text((m, y), line, font=f_t, fill=ink)
        y += int(t_size * 1.16)
    y += 28
    for line in s_lines:
        d.text((m, y), line, font=f_s, fill=ink)
        y += int(s_size * 1.38)
    if s_lines:
        y += 22
    if price:
        tw = d.textlength(price, font=f_p)
        d.rounded_rectangle([m, y, m + tw + 64, y + 96], radius=48, fill=accent)
        txt_c = "#14231e" if sum(_hex(accent)) > 380 else "#ffffff"
        d.text((m + 32, y + 18), price, font=f_p, fill=txt_c)

    # підвал: назва салону, контакт і рядок запису
    fy = H - m
    f_c = _font(_FONT_CANDIDATES, 32)
    f_br = _font(_BOLD_CANDIDATES, 44)
    for line in reversed(lines_bottom):
        fy -= 44
        d.text((m, fy), line, font=f_c, fill=ink)
    if brand:
        fy -= 64
        d.text((m, fy), brand, font=f_br, fill=ink)
    if brand or lines_bottom:
        d.line([(m, fy - 22), (m + 160, fy - 22)], fill=accent, width=6)

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


# ---------- Підписи ----------

DEFAULT_TAGS = "#масаж #косметологія #жовтіВоди #догляд #красаізздоровя"


def _trim(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    cut = text[:n]
    for sep in (". ", "! ", "? "):
        i = cut.rfind(sep)
        if i > n * 0.5:
            return cut[: i + 1]
    return cut.rsplit(" ", 1)[0] + "…"


trim = _trim


def tail(bot_username: str, address: str, phone: str, instagram: str, tags: str) -> str:
    parts = []
    if bot_username:
        parts.append(f"📲 Запис онлайн у Telegram: @{bot_username.lstrip('@')}")
    if phone:
        parts.append(f"📞 {phone}")
    if address:
        parts.append(f"📍 {address}")
    if instagram:
        parts.append(f"📷 {instagram}")
    out = "\n".join(parts)
    if tags:
        out += ("\n\n" if out else "") + tags
    return out


def caption_service(name: str, description: str, duration: int | None, price: str, tail_text: str) -> str:
    head = f"💆 {name}"
    body = _trim(description, 420) if description else ""
    facts = []
    if duration:
        facts.append(f"⏱ {duration} хв")
    if price:
        facts.append(f"💰 {price}")
    out = head + ("\n\n" + body if body else "") + ("\n\n" + " · ".join(facts) if facts else "")
    return out + "\n\n" + tail_text


def caption_windows(lines: list[str], tail_text: str) -> str:
    return "⏰ Вільні вікна найближчими днями:\n\n" + "\n".join(f"• {l}" for l in lines) + "\n\nЗаписуйтесь, поки час вільний 💛\n\n" + tail_text


def caption_tip(title: str, text: str, tail_text: str) -> str:
    return f"💡 {title}\n\n{_trim(text, 700)}\n\nЗбережіть пост, щоб не загубити 💛\n\n{tail_text}"


def caption_custom(title: str, body: str, tail_text: str) -> str:
    return f"{title}\n\n{body}\n\n{tail_text}" if body else f"{title}\n\n{tail_text}"
