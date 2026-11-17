"""Post every supported file type to the running app and report what it says."""
import io
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_import_flow import RTF, docx_bytes, epub_bytes, pdf_bytes  # noqa: E402

BASE = "http://127.0.0.1:8765"
CASES = {
    "probe.pdf": (pdf_bytes(["A sentence on the page. And another sentence here."]), "application/pdf"),
    "probe.epub": (epub_bytes(), "application/epub+zip"),
    "probe.docx": (docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "probe.rtf": (RTF, "application/rtf"),
    "probe.txt": (b"Plain text speaks too. Another sentence follows.", "text/plain"),
    "probe.md": (b"# Head\n\nMarkdown body sentence. One more sentence.", "text/markdown"),
    "probe.html": (b"<html><body><p>Article sentence. Second sentence.</p></body></html>", "text/html"),
}

with httpx.Client(timeout=120) as client:
    for name, (data, mime) in CASES.items():
        try:
            response = client.post(f"{BASE}/api/docs/upload", files={"file": (name, data, mime)})
            body = response.text[:160].replace("\n", " ")
            print(f"{name:12} {response.status_code}  {body}")
        except Exception as exc:  # noqa: BLE001
            print(f"{name:12} EXC     {exc}")

    # An image should now go through Windows OCR.
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (900, 200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("georgia.ttf", 36)
    draw.text((40, 80), "Scanned page reads locally.", font=font, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    response = client.post(
        f"{BASE}/api/docs/upload",
        files={"file": ("probe.png", buffer.getvalue(), "image/png")},
    )
    print(f"{'probe.png':12} {response.status_code}  {response.text[:160]}")
