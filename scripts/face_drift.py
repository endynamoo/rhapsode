"""How consistent are the faces across a chapter? No generation, just the stills.

Detects faces with YuNet (the same detector the polish pass uses), crops each one,
and compares crops by difference hash. Two renders of the same character land within
a few bits of each other; a design drift lands far away. Also writes a contact sheet
of the crops so the number can be checked by eye.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image, ImageDraw

from murmur import detectors


def dhash(image: Image.Image, size: int = 8) -> int:
    """64-bit difference hash: robust to brightness, sensitive to structure."""
    small = image.convert("L").resize((size + 1, size), Image.LANCZOS)
    array = np.asarray(small, dtype=np.int16)
    bits = (array[:, 1:] > array[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def face_crops(path: Path) -> list[Image.Image]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        bgr = np.asarray(rgb)[:, :, ::-1]
        boxes = detectors.faces(bgr)
        crops = []
        for x1, y1, x2, y2 in boxes:
            pad_x = (x2 - x1) * 0.15
            pad_y = (y2 - y1) * 0.25
            box = (int(max(0, x1 - pad_x)), int(max(0, y1 - pad_y)),
                   int(min(rgb.width, x2 + pad_x)), int(min(rgb.height, y2 + pad_y)))
            crops.append(rgb.crop(box))
        return crops


def main(folder: str) -> None:
    root = Path(folder)
    stills = sorted(
        path for path in root.glob("*.png")
        if not any(tag in path.name for tag in ("pre-polish", "fast", "mask", "hands"))
    )
    if not stills:
        raise SystemExit(f"no stills in {root}")

    entries = []
    for path in stills:
        for index, crop in enumerate(face_crops(path)):
            entries.append((path.name, index, crop, dhash(crop)))
    if not entries:
        print("no faces detected")
        return

    print(f"{len(entries)} faces across {len(stills)} stills")
    hashes = [entry[3] for entry in entries]
    distances = [hamming(a, b) for i, a in enumerate(hashes) for b in hashes[i + 1:]]
    if distances:
        print(f"pairwise hamming: mean {np.mean(distances):.1f} "
              f"median {np.median(distances):.0f} max {max(distances)} "
              f"(0 = identical structure, 24+ = a different face)")

    # Cluster by structure so a recurring character can be named "consistent".
    clusters: list[list[int]] = []
    for index, value in enumerate(hashes):
        for cluster in clusters:
            if hamming(value, hashes[cluster[0]]) <= 12:
                cluster.append(index)
                break
        else:
            clusters.append([index])
    clusters.sort(key=len, reverse=True)
    for rank, cluster in enumerate(clusters[:4], start=1):
        names = sorted({entries[i][0][:18] for i in cluster})
        print(f"cluster {rank}: {len(cluster)} faces, {len(names)} stills -> {names[:4]}")

    thumb = 128
    cols = min(8, len(entries))
    rows = (len(entries) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb, rows * (thumb + 14)), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (name, _i, crop, _value) in enumerate(entries):
        x = (index % cols) * thumb
        y = (index // cols) * (thumb + 14)
        sheet.paste(crop.convert("RGB").resize((thumb, thumb), Image.LANCZOS), (x, y))
        draw.text((x + 2, y + thumb), name[3:8], fill="black")
    out = Path.home() / "AppData" / "Local" / "Temp" / "faces_sheet.png"
    sheet.save(out)
    print(f"contact sheet: {out}")


if __name__ == "__main__":
    main(sys.argv[1])
