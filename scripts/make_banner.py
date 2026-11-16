"""Compose the README banner: the ink mark plus the Rhapsode wordmark.

Renders at 2x and downsamples, so the type stays crisp on a Retina screen.
Run from the repo root:  .venv\\Scripts\\python.exe scripts\\make_banner.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "branding" / "quasar-logo-ink-a.png"
OUT = ROOT / "branding" / "banner.png"

W, H = 1600, 400
S = 2  # supersample factor

INK = (18, 16, 15)
BODY = (91, 86, 77)
SPEC = (150, 140, 120)
RULE = (190, 184, 170)

FONT_DIR = Path(r"C:\Windows\Fonts")

# Paper kept around the disc, as a fraction of the disc's own diameter.
PAD = 0.12


def crop_mark(image: Image.Image) -> Image.Image:
    """Crop the inked disc out of the paper, keeping a margin of stock around it."""
    grey = image.convert("L")
    mask = grey.point(lambda v: 255 if v < 128 else 0)
    box = mask.getbbox()
    if box is None:  # fall back to the whole frame
        return image
    left, top, right, bottom = box
    side = max(right - left, bottom - top)
    margin = int(side * PAD)
    cx, cy = (left + right) // 2, (top + bottom) // 2
    half = side // 2 + margin
    return image.crop((cx - half, cy - half, cx + half, cy + half))


def disc_mask(size: int) -> Image.Image:
    """Opaque disc, hard edge, everything outside it dropped.

    The art arrives as a square of cream stock; pasting it as-is leaves a faint
    seam where the two creams meet, so the paper outside the disc is dropped.
    Drawn at 4x and downsampled so the rim stays smooth.
    """
    disc_r = 0.5 / (1.0 + 2 * PAD) + 0.004  # disc radius as a fraction of the square
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    r = disc_r * big
    ImageDraw.Draw(mask).ellipse([big / 2 - r, big / 2 - r, big / 2 + r, big / 2 + r], fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def paper_colour(image: Image.Image) -> tuple[int, int, int]:
    """Average the four corners: the cream stock the mark was drawn on."""
    w, h = image.size
    pts = [(2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3)]
    px = [image.getpixel(p) for p in pts]
    return tuple(sum(c[i] for c in px) // len(px) for i in range(3))


def tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float) -> float:
    """Draw text with letter-spacing; returns the x after the last glyph."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x - tracking


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def main() -> None:
    art = Image.open(SOURCE).convert("RGB")
    paper = paper_colour(art)
    mark = crop_mark(art)

    canvas = Image.new("RGB", (W * S, H * S), paper)
    draw = ImageDraw.Draw(canvas)

    # The mark, optically centred against the wordmark block.
    mark_side = int(H * 0.66) * S
    mark = mark.resize((mark_side, mark_side), Image.LANCZOS)
    mark_x = int(W * 0.048) * S
    mark_y = (H * S - mark_side) // 2 - int(4 * S)
    canvas.paste(mark, (mark_x, mark_y), disc_mask(mark_side))

    text_x = mark_x + mark_side + int(34 * S)

    # Wordmark.
    word_font = font("georgia.ttf", 116 * S)
    word_y = int(H * 0.20) * S
    end = tracked(draw, (text_x, word_y), "Rhapsode", word_font, INK, 1.5 * S)

    # Rule under the wordmark.
    rule_y = word_y + int(150 * S)
    draw.line([(text_x, rule_y), (min(end, text_x + int(560 * S)), rule_y)], fill=RULE, width=S)

    # Tagline.
    tag_font = font("segoeui.ttf", 31 * S)
    draw.text(
        (text_x, rule_y + int(20 * S)),
        "Read-aloud and manga films, entirely on your own machine",
        font=tag_font,
        fill=BODY,
    )

    # Spec line: small caps, tracked out.
    spec_font = font("segoeuib.ttf", 18 * S)
    tracked(
        draw,
        (text_x + int(2 * S), rule_y + int(78 * S)),
        "MACOS · WINDOWS · LINUX      KOKORO-82M      LOCAL ONLY",
        spec_font,
        SPEC,
        2.6 * S,
    )

    canvas.resize((W, H), Image.LANCZOS).save(OUT, optimize=True)
    print(f"wrote {OUT} ({W}x{H}, paper {paper}, mark {mark_side // S}px)")


if __name__ == "__main__":
    main()
