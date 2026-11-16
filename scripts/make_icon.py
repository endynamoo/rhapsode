"""Draw the Rhapsode app icon (quasar) as a 1024px PNG with Pillow."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build_assets" / "icon_1024.png"
SOURCE = ROOT / "branding" / "quasar-logo-ink-a.png"

S = 2048
CX = CY = S // 2
VIOLET = (139, 123, 255)
CYAN = (95, 216, 245)
DEEP = (7, 7, 13)


def overlay(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    if SOURCE.exists():
        art = ImageOps.fit(
            Image.open(SOURCE).convert("RGB"), (1024, 1024), Image.LANCZOS,
        )
        mask = Image.new("L", (1024, 1024), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, 1023, 1023], radius=224, fill=255,
        )
        rounded = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        rounded.paste(art, (0, 0), mask)
        rounded.save(OUT)
        print(f"wrote {OUT} from {SOURCE.name} (1024x1024)")
        return

    image = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = 128
    draw.rounded_rectangle(
        [inset, inset, S - inset, S - inset],
        radius=460,
        fill=DEEP + (255,),
    )

    glow = overlay((S, S))
    gdraw = ImageDraw.Draw(glow)
    for radius, alpha in ((760, 8), (640, 10), (520, 12), (400, 16), (300, 20)):
        gdraw.ellipse(
            [CX - radius, CY - radius, CX + radius, CY + radius],
            fill=VIOLET + (alpha,),
        )
    image = Image.alpha_composite(image, glow)

    jets = overlay((S, S))
    jdraw = ImageDraw.Draw(jets)

    def jet(top: int, bottom: int, flip: bool) -> None:
        w_out, w_in = 22, 10
        y_out, y_in = (top, bottom) if not flip else (bottom, top)
        # Glow first: ImageDraw replaces pixels rather than blending.
        jdraw.polygon(
            [(CX - w_out * 2.1, y_out), (CX + w_out * 2.1, y_out),
             (CX + w_in * 2.2, y_in), (CX - w_in * 2.2, y_in)],
            fill=CYAN + (20,),
        )
        jdraw.polygon(
            [(CX - w_out, y_out), (CX + w_out, y_out), (CX + w_in, y_in), (CX - w_in, y_in)],
            fill=CYAN + (230,),
        )

    jet(536, 896, False)
    jet(1152, 1512, True)
    image = Image.alpha_composite(image, jets)

    disk = overlay((S, S))
    ddraw = ImageDraw.Draw(disk)
    ddraw.ellipse(
        [CX - 584, CY - 156, CX + 584, CY + 156],
        outline=VIOLET + (240,),
        width=52,
    )
    ddraw.ellipse(
        [CX - 572, CY - 144, CX + 572, CY + 144],
        outline=CYAN + (165,),
        width=14,
    )
    image = Image.alpha_composite(image, disk)

    core = overlay((S, S))
    cdraw = ImageDraw.Draw(core)
    for radius, alpha in ((190, 90), (150, 130), (110, 200)):
        cdraw.ellipse([CX - radius, CY - radius, CX + radius, CY + radius], fill=VIOLET + (alpha,))
    cdraw.ellipse([CX - 132, CY - 132, CX + 132, CY + 132], fill=(239, 233, 255, 255))
    cdraw.ellipse([CX - 68, CY - 68, CX + 68, CY + 68], fill=(255, 255, 255, 255))
    image = Image.alpha_composite(image, core)

    image = image.resize((1024, 1024), Image.LANCZOS)
    image.save(OUT)
    print(f"wrote {OUT} (1024x1024)")


if __name__ == "__main__":
    main()
