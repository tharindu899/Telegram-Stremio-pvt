#!/usr/bin/env python3
"""Build compact transparent PNG labels used by the Nuvio badge profile.

Nuvio draws the pill background and coloured border. These assets only draw the
white foreground label and small metadata icon, so the badges stay sharp on any
Nuvio theme.
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Backend" / "fastapi" / "static" / "nuvio_badges"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
WHITE = (248, 250, 252, 255)


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size)


def save(image: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / f"{name}.png", "PNG", optimize=True)


def text_width(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont) -> int:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=fnt)
    return right - left


def draw_globe(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color=WHITE) -> None:
    """Outlined globe icon similar to the reference WebDL chip."""
    stroke = max(2, size // 11)
    box = (x, y, x + size, y + size)
    draw.ellipse(box, outline=color, width=stroke)
    # longitude ellipse and equator/latitude lines
    draw.ellipse((x + size * 0.27, y, x + size * 0.73, y + size), outline=color, width=stroke)
    draw.line((x + stroke, y + size * 0.50, x + size - stroke, y + size * 0.50), fill=color, width=stroke)
    draw.arc((x + size * 0.10, y + size * 0.20, x + size * 0.90, y + size * 0.80), 180, 360, fill=color, width=stroke)
    draw.arc((x + size * 0.10, y + size * 0.20, x + size * 0.90, y + size * 0.80), 0, 180, fill=color, width=stroke)


def draw_speaker(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color=WHITE) -> None:
    """Small speaker with sound waves for surround-sound badges."""
    stroke = max(2, size // 10)
    cy = y + size // 2
    cone_x = x + int(size * 0.40)
    draw.rectangle((x, y + int(size * 0.35), x + int(size * 0.18), y + int(size * 0.65)), fill=color)
    draw.polygon([(x + int(size * 0.18), y + int(size * 0.35)), (cone_x, y + int(size * 0.12)), (cone_x, y + int(size * 0.88)), (x + int(size * 0.18), y + int(size * 0.65))], fill=color)
    draw.arc((x + int(size * 0.30), y + int(size * 0.18), x + int(size * 0.90), y + int(size * 0.82)), -55, 55, fill=color, width=stroke)
    draw.arc((x + int(size * 0.38), y + int(size * 0.02), x + int(size * 1.08), y + int(size * 0.98)), -50, 50, fill=color, width=stroke)


def draw_dolby_mark(image: Image.Image, x: int, y: int, h: int, color=WHITE) -> int:
    """Draw a clean double-D audio glyph for the compact Dolby-style badge."""
    glyph_font = font(int(h * 0.86))
    # Render one D then mirror it so the two characters form a compact
    # double-D symbol without relying on an external trademark artwork file.
    bbox = glyph_font.getbbox("D")
    glyph_w = bbox[2] - bbox[0]
    glyph_h = bbox[3] - bbox[1]
    glyph = Image.new("RGBA", (glyph_w + 2, glyph_h + 2), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glyph)
    gd.text((0, -bbox[1]), "D", font=glyph_font, fill=color)
    offset_y = y + (h - glyph.height) // 2
    image.alpha_composite(glyph, (x, offset_y))
    image.alpha_composite(glyph.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (x + glyph_w - 1, offset_y))
    return glyph_w * 2 - 1


def draw_web_badge(name: str, label: str) -> None:
    h = 52
    fnt = font(27)
    pad = 8
    icon = 26
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    width = pad + icon + 7 + text_width(d, label, fnt) + pad
    im = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw_globe(draw, pad, (h - icon) // 2, icon)
    draw.text((pad + icon + 7, 9), label, font=fnt, fill=WHITE)
    save(im, name)


def draw_audio_badge(name: str, main: str, sub: str | None = None) -> None:
    h = 52
    pad = 7
    mark_h = 26
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    top_font = font(10)
    bottom_font = font(15)
    mark_w = int(mark_h * 1.45)
    label_w = max(text_width(d, main, bottom_font), text_width(d, sub or "", top_font))
    width = pad + mark_w + 7 + label_w + pad
    im = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw_dolby_mark(im, pad, (h - mark_h) // 2, mark_h)
    tx = pad + mark_w + 7
    if sub:
        draw.text((tx, 8), sub, font=top_font, fill=WHITE)
        draw.text((tx, 24), main, font=bottom_font, fill=WHITE)
    else:
        draw.text((tx, 17), main, font=bottom_font, fill=WHITE)
    save(im, name)


def draw_surround_badge(name: str, label: str) -> None:
    h = 52
    pad = 8
    icon = 25
    fnt = font(27)
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    d = ImageDraw.Draw(probe)
    width = pad + icon + 6 + text_width(d, label, fnt) + pad
    im = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw_speaker(draw, pad, (h - icon) // 2, icon)
    draw.text((pad + icon + 6, 9), label, font=fnt, fill=WHITE)
    save(im, name)


def main() -> None:
    draw_web_badge("webdl", "WebDL")
    draw_web_badge("webrip", "WebRip")
    draw_audio_badge("ddplus", "DIGITAL+", "DOLBY")
    draw_audio_badge("dd", "DIGITAL", "DOLBY")
    draw_surround_badge("51", "5.1")
    draw_surround_badge("71", "7.1")


if __name__ == "__main__":
    main()
