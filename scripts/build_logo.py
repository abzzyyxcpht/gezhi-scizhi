# -*- coding: utf-8 -*-
"""Build the SciZhi identity from shared, font-independent vector geometry.

Run ``python scripts/build_logo.py`` to regenerate the SVG, 512 px PNG,
seven-size Windows ICO, transparent wordmark, and light/dark preview sheet.
Only Pillow is required. Fonts are used for the wordmark and preview labels;
the app icon is made entirely from the geometry below.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 512
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

FOREST = "#286557"
WHITE = "#FFFFFF"
INK = "#242B30"
MUTED = "#687780"
PAPER = "#F3F5F6"
NIGHT = "#182923"

# Canonical 512-unit geometry, used directly by both SVG and Pillow.
# The right leaf releases its upper corner into a single research point.
FIELD = (24, 24, 488, 488)
CORNER_RADIUS = 104
LEFT_PAGE = ((112, 160), (204, 176), (240, 196),
             (240, 356), (204, 336), (112, 320))
RIGHT_PAGE = ((272, 196), (306, 178), (340, 172), (340, 212),
              (400, 194), (400, 320), (308, 336), (272, 356))
RESEARCH_POINT = (384, 140, 22)

FONT_DIRS = (
    Path(r"C:\Windows\Fonts"),
    Path(r"C:\Windows\Fonts\Fallback"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/share/fonts/opentype/noto"),
    Path("/System/Library/Fonts"),
)
LATIN_FONTS = ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf")
CJK_FONTS = ("msyh.ttc", "simhei.ttf", "NotoSansCJK-Regular.ttc", "PingFang.ttc")
LABEL_FONTS = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")


def font(candidates: tuple[str, ...], px: int) -> ImageFont.FreeTypeFont:
    """Use installed fonts in priority order, keeping Chinese glyphs intact."""
    for name in candidates:
        for directory in FONT_DIRS:
            path = directory / name
            if path.is_file():
                return ImageFont.truetype(str(path), px)
    raise FileNotFoundError(
        "No suitable font found. Install one of: " + ", ".join(candidates)
    )


def draw_icon(px: int = SIZE) -> Image.Image:
    """Render the canonical geometry with transparent rounded corners."""
    render_size = max(1024, px * 4)
    scale = render_size / SIZE
    canvas = Image.new("RGBA", (render_size, render_size))
    draw = ImageDraw.Draw(canvas)
    bounds = tuple(value * scale for value in FIELD)
    # Pillow's rectangle includes the last pixel; SVG's width does not.
    draw.rounded_rectangle(
        (bounds[0], bounds[1], bounds[2] - 1, bounds[3] - 1),
        radius=CORNER_RADIUS * scale,
        fill=FOREST,
    )
    for page in (LEFT_PAGE, RIGHT_PAGE):
        draw.polygon([(x * scale, y * scale) for x, y in page], fill=WHITE)
    cx, cy, radius = RESEARCH_POINT
    draw.ellipse(
        ((cx - radius) * scale, (cy - radius) * scale,
         (cx + radius) * scale, (cy + radius) * scale),
        fill=WHITE,
    )
    return canvas.resize((px, px), Image.Resampling.LANCZOS)


def svg_source() -> str:
    """The scalable master contains geometry only, with no font dependency."""
    def points(page: tuple[tuple[int, int], ...]) -> str:
        return " ".join(f"{x},{y}" for x, y in page)

    x0, y0, x1, y1 = FIELD
    cx, cy, radius = RESEARCH_POINT
    description = escape("SciZhi / 格致：展开的书页与一枚研究坐标圆点。")
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512"
     viewBox="0 0 512 512" role="img" aria-labelledby="title description">
  <title id="title">SciZhi / 格致</title>
  <desc id="description">{description}</desc>
  <rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}"
        rx="{CORNER_RADIUS}" fill="{FOREST}"/>
  <g fill="{WHITE}">
    <polygon points="{points(LEFT_PAGE)}"/>
    <polygon points="{points(RIGHT_PAGE)}"/>
    <circle cx="{cx}" cy="{cy}" r="{radius}"/>
  </g>
</svg>
'''


def draw_wordmark(color: str = INK) -> Image.Image:
    """Horizontal icon + SciZhi / 格致, without a tagline."""
    scale = 3
    canvas = Image.new("RGBA", (1160 * scale, 280 * scale))
    draw = ImageDraw.Draw(canvas)
    canvas.alpha_composite(draw_icon(224 * scale), (20 * scale, 28 * scale))
    # Each script is optically centered by the actual ink, not font metrics.
    runs = (("SciZhi", LATIN_FONTS, 116, 42),
            ("/", LABEL_FONTS, 84, 42),
            ("格致", CJK_FONTS, 100, 0))
    x = 288 * scale
    for text, candidates, size, gap in runs:
        face = font(candidates, size * scale)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
        y = (280 * scale - (bottom - top)) / 2 - top
        draw.text((x - left, y), text, font=face, fill=color)
        x += right - left + gap * scale
    width = min(canvas.width, int(x + 32 * scale))
    canvas = canvas.crop((0, 0, width, 280 * scale))
    return canvas.resize((round(width / scale), 280), Image.Resampling.LANCZOS)


def draw_preview() -> Image.Image:
    """Show the identity and the actual native icon sizes on both surfaces."""
    canvas = Image.new("RGB", (1440, 960), PAPER)
    draw = ImageDraw.Draw(canvas)
    title_font = font(LATIN_FONTS, 42)
    label_font = font(LABEL_FONTS, 17)
    small_font = font(LABEL_FONTS, 15)
    draw.text((64, 48), "SciZhi", font=title_font, fill=INK)
    draw.text((222, 56), "/", font=font(LABEL_FONTS, 32), fill=MUTED)
    draw.text((252, 53), "格致", font=font(CJK_FONTS, 35), fill=INK)
    draw.text((64, 112), "Logo, wordmark and application sizes", font=label_font, fill=MUTED)
    draw.text((1204, 62), "BRAND ASSETS", font=small_font, fill=MUTED)

    for x, background, foreground, secondary, label in (
        (64, WHITE, INK, MUTED, "LIGHT"),
        (744, NIGHT, WHITE, "#B1C3BA", "DARK"),
    ):
        draw.rounded_rectangle((x, 176, x + 632, 838), radius=24, fill=background)
        draw.text((x + 32, 205), label, font=small_font, fill=secondary)
        large_icon = draw_icon(176)
        canvas.paste(large_icon, (x + 228, 261), large_icon)
        wordmark = draw_wordmark(foreground)
        wordmark.thumbnail((540, 140), Image.Resampling.LANCZOS)
        canvas.paste(wordmark, (x + (632 - wordmark.width) // 2, 462), wordmark)
        draw.line((x + 32, 622, x + 600, 622), fill=PAPER if label == "LIGHT" else "#31433A")
        for size, center in zip((16, 24, 32, 64, 128), (65, 154, 249, 366, 520)):
            icon = draw_icon(size)
            canvas.paste(icon, (x + center - size // 2, 710 - size // 2), icon)
            caption = f"{size} px"
            caption_width = draw.textlength(caption, font=small_font)
            draw.text((x + center - caption_width / 2, 795), caption,
                      font=small_font, fill=secondary)

    draw.rounded_rectangle((64, 884, 88, 908), radius=6, fill=FOREST)
    draw.text((103, 885), "Forest  #286557", font=small_font, fill=INK)
    draw.text((1096, 885), "SVG / PNG / Windows ICO", font=small_font, fill=MUTED)
    return canvas


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "gezhi.svg").write_text(svg_source(), encoding="utf-8")
    draw_icon().save(ASSETS / "gezhi.png")
    # Every frame is rendered from the same geometry, including 16 and 24 px.
    frames = [draw_icon(size) for size in ICO_SIZES]
    frames[-1].save(
        ASSETS / "gezhi.ico", sizes=[(size, size) for size in ICO_SIZES],
        append_images=frames[:-1],
    )
    draw_wordmark().save(ASSETS / "gezhi-wordmark.png")
    draw_preview().save(ASSETS / "brand-preview.png")
    for name in ("gezhi.svg", "gezhi.png", "gezhi.ico", "gezhi-wordmark.png", "brand-preview.png"):
        print(f"Generated assets/{name}")


if __name__ == "__main__":
    main()
