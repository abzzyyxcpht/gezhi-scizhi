# -*- coding: utf-8 -*-
"""Build the SciZhi identity from shared, font-independent vector geometry.

Run ``python scripts/build_logo.py`` to regenerate the SVG, 512 px PNG,
ten-size Windows ICO, and transparent wordmark. Review sheets and individual
ICO frames are written to ``build/icon-review``.
Only Pillow is required. Fonts are used for the wordmark and preview labels;
the app icon is made entirely from the geometry below.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
REVIEW = ROOT / "build" / "icon-review"
SIZE = 512
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

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


@dataclass(frozen=True)
class IconGeometry:
    """Coordinates in the target image's pixels, before supersampling."""

    field: tuple[float, float, float, float]
    corner: float
    left: tuple[tuple[float, float], ...]
    right: tuple[tuple[float, float], ...]
    point: tuple[float, float, float]


# Native-size optical masters. Integer vertical edges keep the page silhouettes
# and spine crisp; the smallest research dots are enlarged to remain distinct.
# These deliberately differ from simply shrinking the 512-unit brand master.
OPTICAL_MASTERS = {
    16: IconGeometry(
        (1, 1, 15, 15), 3,
        ((3, 5), (5.5, 5.5), (7, 6.25), (7, 11.5), (5.5, 10.75), (3, 10.25)),
        ((8, 6.25), (9.25, 5.5), (10.5, 5.25), (10.5, 7),
         (13, 6.25), (13, 10.25), (9.5, 10.75), (8, 11.5)),
        (12, 4, 1),
    ),
    20: IconGeometry(
        (1, 1, 19, 19), 4,
        ((4, 6.25), (7.5, 6.875), (9, 7.75), (9, 14), (7.25, 13.25), (4, 12.5)),
        ((11, 7.75), (12, 7), (13, 6.75), (13, 8.5),
         (16, 7.5), (16, 12.5), (12.5, 13.25), (11, 14)),
        (15, 5.25, 1.1),
    ),
    24: IconGeometry(
        (1, 1, 23, 23), 5,
        ((5, 7.5), (9.5, 8.25), (11, 9.25), (11, 17), (9.5, 16), (5, 15)),
        ((13, 9.25), (14.25, 8.5), (16, 8), (16, 10),
         (19, 9.25), (19, 15), (14.5, 16), (13, 17)),
        (18, 6.5, 1.25),
    ),
    32: IconGeometry(
        (2, 2, 30, 30), 6.5,
        ((7, 10), (12.75, 11), (15, 12.25), (15, 22.25), (12.75, 21), (7, 20)),
        ((17, 12.25), (19, 11), (21, 10.75), (21, 13.25),
         (25, 12.25), (25, 20), (19.25, 21), (17, 22.25)),
        (24, 8.75, 1.5),
    ),
    40: IconGeometry(
        (2, 2, 38, 38), 8,
        ((9, 12.5), (16, 13.75), (19, 15.25), (19, 28), (16, 26.25), (9, 25)),
        ((21, 15.25), (24, 13.875), (27, 13.5), (27, 16.75),
         (31, 15.25), (31, 25), (24, 26.25), (21, 28)),
        (30, 11, 1.75),
    ),
    48: IconGeometry(
        (2, 2, 46, 46), 10,
        ((10, 15), (19, 16.5), (22, 18.25), (22, 33.5), (19, 31.5), (10, 30)),
        ((26, 18.25), (28.75, 16.75), (32, 16), (32, 20),
         (38, 18), (38, 30), (29, 31.5), (26, 33.5)),
        (36, 13, 2.25),
    ),
    64: IconGeometry(
        (3, 3, 61, 61), 13,
        ((14, 20), (25.5, 22), (30, 24.5), (30, 44.5), (25.5, 42), (14, 40)),
        ((34, 24.5), (38.25, 22.25), (42, 21.5), (42, 26.5),
         (50, 24.25), (50, 40), (38.5, 42), (34, 44.5)),
        (48, 17.5, 3),
    ),
}

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
    """Render the optical master when available, retaining real alpha edges."""
    scale = 8 if px <= 128 else 4
    render_size = px * scale
    geometry = OPTICAL_MASTERS.get(px)
    if geometry is None:
        ratio = px / SIZE
        geometry = IconGeometry(
            tuple(value * ratio for value in FIELD),
            CORNER_RADIUS * ratio,
            tuple((x * ratio, y * ratio) for x, y in LEFT_PAGE),
            tuple((x * ratio, y * ratio) for x, y in RIGHT_PAGE),
            tuple(value * ratio for value in RESEARCH_POINT),
        )
    canvas = Image.new("RGBA", (render_size, render_size))
    draw = ImageDraw.Draw(canvas)
    bounds = tuple(value * scale for value in geometry.field)
    # Pillow's rectangle includes the last pixel; SVG's width does not.
    draw.rounded_rectangle(
        (bounds[0], bounds[1], bounds[2] - 1, bounds[3] - 1),
        radius=geometry.corner * scale,
        fill=FOREST,
    )
    for page in (geometry.left, geometry.right):
        # Pillow includes boundary pixels; the half-pixel offset centers its
        # coverage on the vector edge instead of thickening the right edge.
        draw.polygon([(x * scale - 0.5, y * scale - 0.5) for x, y in page], fill=WHITE)
    cx, cy, radius = geometry.point
    draw.ellipse(
        ((cx - radius) * scale, (cy - radius) * scale,
         (cx + radius) * scale - 1, (cy + radius) * scale - 1),
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


def draw_size_preview() -> Image.Image:
    """Show every embedded Windows frame at its actual pixel size."""
    canvas = Image.new("RGB", (1360, 760), PAPER)
    draw = ImageDraw.Draw(canvas)
    label = font(LABEL_FONTS, 16)
    draw.text((40, 28), "SciZhi / Windows icon sizes at 100%", font=font(LATIN_FONTS, 28), fill=INK)
    for y, background, foreground, surface in (
        (96, WHITE, INK, "LIGHT"),
        (416, NIGHT, WHITE, "DARK"),
    ):
        draw.rounded_rectangle((24, y, 1336, y + 304), radius=20, fill=background)
        draw.text((48, y + 18), surface, font=label, fill=foreground)
        x = 80
        for size in ICO_SIZES:
            icon = draw_icon(size)
            canvas.paste(icon, (x, y + 146 - size // 2), icon)
            caption = f"{size} px"
            width = draw.textlength(caption, font=label)
            draw.text((x + (size - width) / 2, y + 280), caption, font=label, fill=foreground)
            x += size + 48
    return canvas


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    REVIEW.mkdir(parents=True, exist_ok=True)
    (ASSETS / "gezhi.svg").write_text(svg_source(), encoding="utf-8")
    draw_icon().save(ASSETS / "gezhi.png")
    # Pass each independently rendered frame: Pillow must not scale one frame
    # into the others and lose the native-size optical adjustments.
    frames = [draw_icon(size) for size in ICO_SIZES]
    frames[-1].save(
        ASSETS / "gezhi.ico", sizes=[(size, size) for size in ICO_SIZES],
        append_images=frames[:-1],
    )
    draw_wordmark().save(ASSETS / "gezhi-wordmark.png")
    for size, frame in zip(ICO_SIZES, frames):
        frame.save(REVIEW / f"icon-{size}.png")
    draw_preview().save(REVIEW / "brand-preview.png")
    draw_size_preview().save(REVIEW / "windows-icon-sizes.png")
    for name in ("gezhi.svg", "gezhi.png", "gezhi.ico", "gezhi-wordmark.png"):
        print(f"Generated assets/{name}")
    print("Generated review sheets and native frames in build/icon-review")


if __name__ == "__main__":
    main()
