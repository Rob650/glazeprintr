"""GlazePrintr branded image generation."""
import io
import logging
import random
import re
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

FONT_DIR = Path("/tmp/glazeprintr_fonts")
IMG_DIR = Path("/tmp/glazeprintr_images")

# Brand palette
BG = (8, 8, 14)
NEON_PINK = (255, 20, 180)
NEON_PURPLE = (140, 30, 255)
NEON_BLUE = (30, 180, 255)
NEON_ORANGE = (255, 110, 20)
WHITE = (255, 255, 255)
GRAY = (130, 130, 150)
DARK = (25, 25, 40)

TIER_COLOR = {
    "MAXIMUM GLAZE": NEON_ORANGE,
    "Heavy Glazer": NEON_PINK,
    "Solid Shill": NEON_PURPLE,
    "Light Glaze": NEON_BLUE,
    "Casual Mention": GRAY,
}

_FONT_URLS = {
    "bold": "https://github.com/googlefonts/Orbitron/raw/main/fonts/ttf/Orbitron-Bold.ttf",
    "regular": "https://github.com/googlefonts/Orbitron/raw/main/fonts/ttf/Orbitron-Regular.ttf",
}

_SYSTEM_FALLBACKS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]

_font_cache: dict = {}


def _ensure_dirs():
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)


def _get_font(style: str, size: int) -> ImageFont.ImageFont:
    key = (style, size)
    if key in _font_cache:
        return _font_cache[key]

    _ensure_dirs()
    path = FONT_DIR / f"orbitron_{style}.ttf"

    if not path.exists():
        url = _FONT_URLS.get(style, _FONT_URLS["regular"])
        try:
            logger.info(f"Downloading font {style}")
            urllib.request.urlretrieve(url, str(path))
        except Exception as e:
            logger.warning(f"Font download failed ({style}): {e}")

    if path.exists():
        try:
            font = ImageFont.truetype(str(path), size)
            _font_cache[key] = font
            return font
        except Exception as e:
            logger.warning(f"Font truetype failed: {e}")

    for sys_path in _SYSTEM_FALLBACKS:
        if Path(sys_path).exists():
            try:
                font = ImageFont.truetype(sys_path, size)
                _font_cache[key] = font
                return font
            except Exception:
                pass

    return ImageFont.load_default()


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if _tw(draw, test, font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _glow_text(draw: ImageDraw.ImageDraw, xy, text, font, color, glow=4):
    x, y = xy
    r, g, b = color
    gc = (min(r + 60, 255), min(g + 60, 255), min(b + 60, 255))
    for s in range(glow, 0, -1):
        a = max(8, 50 // s)
        for dx, dy in [(-s, 0), (s, 0), (0, -s), (0, s)]:
            draw.text((x + dx, y + dy), text, font=font, fill=(*gc, a))
    draw.text(xy, text, font=font, fill=color)


def _neon_rect(draw: ImageDraw.ImageDraw, box, color, width=2):
    x1, y1, x2, y2 = box
    r, g, b = color
    gc = (min(r + 60, 255), min(g + 60, 255), min(b + 60, 255))
    for s in range(5, 0, -1):
        draw.rectangle([x1-s, y1-s, x2+s, y2+s], outline=(*gc, 18), width=1)
    draw.rectangle(box, outline=color, width=width)


def _crosshair(draw, cx, cy, size=20, color=NEON_BLUE):
    gap = size // 4
    arm = size // 2
    c = (*color, 80)
    draw.line([(cx-arm, cy), (cx-gap, cy)], fill=c, width=1)
    draw.line([(cx+gap, cy), (cx+arm, cy)], fill=c, width=1)
    draw.line([(cx, cy-arm), (cx, cy-gap)], fill=c, width=1)
    draw.line([(cx, cy+gap), (cx, cy+arm)], fill=c, width=1)
    draw.rectangle([cx-gap, cy-gap, cx+gap, cy+gap], outline=c, width=1)


def _progress_bar(draw, x1, y1, x2, y2, pct, color):
    draw.rectangle([x1, y1, x2, y2], fill=DARK, outline=(*GRAY, 80), width=1)
    fw = int((x2 - x1) * max(0.0, min(1.0, pct)))
    if fw > 0:
        draw.rectangle([x1, y1, x1 + fw, y2], fill=color)


def _grid_bg(draw, W, H):
    for x in range(0, W, 60):
        draw.line([(x, 0), (x, H)], fill=(20, 20, 35, 40), width=1)
    for y in range(0, H, 60):
        draw.line([(0, y), (W, y)], fill=(20, 20, 35, 40), width=1)


def _scanlines(draw, W, H, alpha=14):
    for y in range(0, H, 4):
        draw.line([(0, y), (W, y)], fill=(0, 0, 10, alpha), width=1)


def _save(img: Image.Image, path: Path) -> str:
    out = img if img.mode == "RGB" else img.convert("RGB")
    out.save(str(path), "PNG", optimize=True)
    return str(path)


def generate_glaze_score_card(
    score: int,
    tier: str,
    token_name: str,
    score_card_text: str,
    metrics: dict | None = None,
) -> str | None:
    """Generate a glaze score card image. Returns PNG path or None on failure."""
    _ensure_dirs()
    try:
        W, H = 1200, 675
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img, "RGBA")

        _grid_bg(draw, W, H)

        tier_color = TIER_COLOR.get(tier, NEON_PURPLE)

        _neon_rect(draw, [8, 8, W-9, H-9], tier_color, width=3)
        _neon_rect(draw, [18, 18, W-19, H-19], NEON_PURPLE, width=1)

        for cx, cy in [(28, 28), (W-28, 28), (28, H-28), (W-28, H-28)]:
            draw.ellipse([cx-6, cy-6, cx+6, cy+6], fill=tier_color)

        f_header = _get_font("bold", 40)
        f_score  = _get_font("bold", 118)
        f_slash  = _get_font("bold", 48)
        f_tier   = _get_font("bold", 28)
        f_token  = _get_font("bold", 24)
        f_small  = _get_font("regular", 18)
        f_tiny   = _get_font("regular", 14)

        # "GLAZE SCORE™" header
        hw = _tw(draw, "GLAZE SCORE™", f_header)
        _glow_text(draw, ((W - hw) // 2, 26), "GLAZE SCORE™", f_header, NEON_PINK)

        _crosshair(draw, 72, 54, size=32, color=NEON_BLUE)
        _crosshair(draw, W-72, 54, size=32, color=NEON_BLUE)

        draw.line([(60, 88), (W-60, 88)], fill=(*NEON_PURPLE, 160), width=1)

        # Large score number
        score_str = str(score)
        sw = _tw(draw, score_str, f_score)
        sx = W // 2 - sw // 2 - 30
        sy = 102
        for spread in [14, 9, 4]:
            draw.text((sx - spread//2, sy + spread//2), score_str, font=f_score,
                      fill=(*tier_color, 18))
        draw.text((sx, sy), score_str, font=f_score, fill=tier_color)

        # /100 to the right
        draw.text((sx + sw + 10, sy + 68), "/100", font=f_slash, fill=(*GRAY, 200))

        # Tier label
        tier_y = sy + 132
        tier_str = tier.upper()
        tw_val = _tw(draw, tier_str, f_tier)
        _glow_text(draw, ((W - tw_val) // 2, tier_y), tier_str, f_tier, tier_color, glow=2)

        # Token cashtag
        if token_name:
            cashtag = token_name.upper() if token_name.startswith("$") else f"${token_name.upper()}"
            tkw = _tw(draw, cashtag, f_token)
            draw.text(((W - tkw) // 2, tier_y + 44), cashtag, font=f_token, fill=WHITE)

        sep_y = tier_y + 94
        draw.line([(60, sep_y), (W-60, sep_y)], fill=(*NEON_PURPLE, 100), width=1)

        # Metrics row
        if metrics:
            mx_y = sep_y + 18
            cols  = [80, 340, 590, 840]
            labels = ["POB STAKED", "MARKET CAP", "24H VOLUME", "24H CHANGE"]

            staking = metrics.get("staking_pct")
            mc      = metrics.get("market_cap")
            vol     = metrics.get("volume")
            chg     = metrics.get("price_change_24h")

            def mc_fmt(v):
                if v is None:
                    return None
                return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v:,.0f}"

            vals = [
                (f"{staking:.0f}%" if staking is not None else None, NEON_ORANGE),
                (mc_fmt(mc), NEON_BLUE),
                (mc_fmt(vol), NEON_BLUE),
                (None, GRAY),
            ]
            if chg is not None:
                chg_color = (80, 255, 120) if chg >= 0 else (255, 80, 80)
                vals[3] = (f"{chg:+.1f}%", chg_color)

            for i, (col_x, label, (val_str, val_color)) in enumerate(
                zip(cols, labels, vals)
            ):
                draw.text((col_x, mx_y), label, font=f_tiny, fill=(*GRAY, 180))
                if val_str:
                    draw.text((col_x, mx_y + 22), val_str, font=f_small, fill=val_color)
                if i == 0 and staking is not None:
                    _progress_bar(draw, col_x, mx_y + 50, col_x + 220, mx_y + 62,
                                  staking / 100, NEON_ORANGE)

        # Score card text
        text_y = H - 116
        draw.line([(60, text_y - 14), (W-60, text_y - 14)], fill=(*NEON_PURPLE, 80), width=1)
        lines = _wrap_text(draw, score_card_text, f_small, W - 160)
        for i, line in enumerate(lines[:3]):
            draw.text((80, text_y + i * 28), line, font=f_small, fill=WHITE)

        # Watermark
        wm = "PrintrGlazr  ·  app.printr.money"
        wmw = _tw(draw, wm, f_tiny)
        draw.text(((W - wmw) // 2, H - 28), wm, font=f_tiny, fill=(*GRAY, 140))

        _scanlines(draw, W, H)

        safe = re.sub(r"[^a-z0-9_]", "", (token_name or "unknown").lower().replace("$", ""))
        out = IMG_DIR / f"glaze_{score}_{safe}.png"
        return _save(img, out)

    except Exception as e:
        logger.error(f"generate_glaze_score_card failed: {e}", exc_info=True)
        return None


def generate_ecosystem_stats_card(projects: list[dict]) -> str | None:
    """Generate ecosystem stats card. Returns PNG path or None on failure."""
    _ensure_dirs()
    try:
        W, H = 1200, 675
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img, "RGBA")

        _grid_bg(draw, W, H)
        _neon_rect(draw, [8, 8, W-9, H-9], NEON_PURPLE, width=3)
        _neon_rect(draw, [18, 18, W-19, H-19], NEON_PINK, width=1)

        for cx, cy in [(28, 28), (W-28, 28), (28, H-28), (W-28, H-28)]:
            draw.ellipse([cx-6, cy-6, cx+6, cy+6], fill=NEON_PINK)

        f_header = _get_font("bold", 36)
        f_sub    = _get_font("regular", 14)
        f_med    = _get_font("bold", 20)
        f_small  = _get_font("regular", 17)
        f_tiny   = _get_font("regular", 13)

        hdr = "PRINTR ECOSYSTEM STATS"
        hw = _tw(draw, hdr, f_header)
        _glow_text(draw, ((W - hw) // 2, 24), hdr, f_header, NEON_PINK)

        sub = "REAL-TIME GLAZE SURVEILLANCE"
        sw = _tw(draw, sub, f_sub)
        draw.text(((W - sw) // 2, 76), sub, font=f_sub, fill=(*NEON_BLUE, 200))

        _crosshair(draw, 50, 55, size=26, color=NEON_BLUE)
        _crosshair(draw, W-50, 55, size=26, color=NEON_BLUE)

        draw.line([(60, 104), (W-60, 104)], fill=(*NEON_PURPLE, 180), width=1)

        # Column headers
        COL = [80, 300, 490, 650, 810]
        hdr_y = 116
        for txt, x in zip(["TOKEN", "MARKET CAP", "24H", "VOLUME", "POB STAKED"], COL):
            draw.text((x, hdr_y), txt, font=f_tiny, fill=(*GRAY, 200))
        draw.line([(60, hdr_y + 20), (W-60, hdr_y + 20)], fill=(*DARK, 255), width=1)

        top = sorted(
            [p for p in projects if p.get("market_cap")],
            key=lambda p: p["market_cap"],
            reverse=True,
        )[:8]

        PALETTE = [NEON_PINK, NEON_PURPLE, NEON_BLUE, NEON_ORANGE,
                   (80, 255, 180), NEON_PINK, NEON_BLUE, NEON_ORANGE]
        ROW_H = 57

        for i, proj in enumerate(top):
            ry = hdr_y + 26 + i * ROW_H
            rc = PALETTE[i % len(PALETTE)]

            if i % 2 == 0:
                draw.rectangle([62, ry - 2, W-62, ry + ROW_H - 6], fill=(20, 20, 40, 50))

            name = proj.get("name", "?")
            draw.text((COL[0], ry + 6), f"${name.upper()}", font=f_med, fill=rc)

            mc = proj.get("market_cap", 0) or 0
            mc_str = f"${mc/1e6:.2f}M" if mc >= 1e6 else f"${mc:,.0f}"
            draw.text((COL[1], ry + 6), mc_str, font=f_small, fill=WHITE)

            chg = proj.get("price_change_24h")
            if chg is not None:
                chg_color = (80, 255, 120) if chg >= 0 else (255, 80, 80)
                draw.text((COL[2], ry + 6), f"{chg:+.1f}%", font=f_small, fill=chg_color)

            vol = proj.get("volume")
            if vol is not None:
                vol_str = f"${vol/1e6:.2f}M" if vol >= 1e6 else f"${vol:,.0f}"
                draw.text((COL[3], ry + 6), vol_str, font=f_small, fill=(*GRAY, 220))

            stk = proj.get("staking_pct")
            if stk is not None:
                draw.text((COL[4], ry + 6), f"{stk:.0f}%", font=f_small, fill=NEON_ORANGE)
                _progress_bar(draw, COL[4]+55, ry+12, COL[4]+240, ry+24, stk/100, NEON_ORANGE)

        draw.line([(60, H-44), (W-60, H-44)], fill=(*NEON_PURPLE, 80), width=1)
        wm = "PrintrGlazr  ·  app.printr.money  ·  dune.com/defioasis/printr"
        wmw = _tw(draw, wm, f_tiny)
        draw.text(((W - wmw) // 2, H-30), wm, font=f_tiny, fill=(*GRAY, 140))

        _scanlines(draw, W, H)

        out = IMG_DIR / "ecosystem_stats.png"
        return _save(img, out)

    except Exception as e:
        logger.error(f"generate_ecosystem_stats_card failed: {e}", exc_info=True)
        return None


def remix_tweet_image(image_url: str, token_name: str = "", score: int | None = None) -> str | None:
    """
    Download an image from a tweet and overlay GlazePrintr branding.
    Returns PNG path or None on failure.
    """
    _ensure_dirs()
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = resp.read()

        src = Image.open(io.BytesIO(data)).convert("RGB")

        # Resize/crop to 1200x675
        W, H = 1200, 675
        src_aspect = src.width / src.height
        target_aspect = W / H
        if src_aspect > target_aspect:
            new_h = H
            new_w = int(H * src_aspect)
        else:
            new_w = W
            new_h = int(W / src_aspect)
        src = src.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - W) // 2
        top_px = (new_h - H) // 2
        src = src.crop((left, top_px, left + W, top_px + H))

        draw = ImageDraw.Draw(src, "RGBA")

        # Neon frame
        _neon_rect(draw, [8, 8, W-9, H-9], NEON_PURPLE, width=3)
        _neon_rect(draw, [18, 18, W-19, H-19], NEON_PINK, width=1)

        f_med  = _get_font("bold", 22)
        f_small = _get_font("bold", 18)
        f_tiny = _get_font("regular", 14)

        # Bottom branding strip
        draw.rectangle([0, H-68, W, H], fill=(*BG, 210))
        draw.line([(0, H-68), (W, H-68)], fill=(*NEON_PINK, 200), width=2)

        if score is not None:
            label = f"GLAZE SCORE™  {score}/100"
            draw.text((28, H-52), label, font=f_small, fill=NEON_ORANGE)

        if token_name:
            cashtag = token_name.upper() if token_name.startswith("$") else f"${token_name.upper()}"
            draw.text((28, H-28), cashtag, font=f_tiny, fill=(*WHITE, 200))

        wm = "PrintrGlazr  ·  app.printr.money"
        wmw = _tw(draw, wm, f_tiny)
        draw.text((W - wmw - 20, H-38), wm, font=f_tiny, fill=(*GRAY, 200))

        # Score badge top-right corner
        if score is not None:
            f_badge = _get_font("bold", 30)
            badge_str = str(score)
            bw = _tw(draw, badge_str, f_badge) + 28
            bx1, by1, bx2, by2 = W - bw - 12, 12, W - 12, 12 + 52
            draw.rectangle([bx1, by1, bx2, by2], fill=(*BG, 220))
            _neon_rect(draw, [bx1, by1, bx2, by2], NEON_ORANGE, width=2)
            draw.text((bx1 + 10, by1 + 8), badge_str, font=f_badge, fill=NEON_ORANGE)

        _scanlines(draw, W, H, alpha=10)

        safe = re.sub(r"[^a-z0-9_]", "", (token_name or "meme").lower().replace("$", ""))
        out = IMG_DIR / f"remix_{safe}_{score or 'x'}.png"
        return _save(src, out)

    except Exception as e:
        logger.error(f"remix_tweet_image failed: {e}", exc_info=True)
        return None
