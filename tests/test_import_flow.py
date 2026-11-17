"""Import a real file through the real server, the way the UI does.

The unit tests never posted an upload, which is how a broken temp-file handoff
shipped: PDFium could not open the file on Windows and every PDF import returned
an opaque internal error. These tests drive the HTTP endpoint with a file of each
supported kind, so a platform regression fails here instead of in someone's app.
"""
from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from murmur import config, server, store


@contextmanager
def temp_home():
    """Point every data path at a throwaway directory for the test."""
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        with mock.patch.multiple(
            config,
            HOME=base,
            MODELS_DIR=base / "models",
            CACHE_DIR=base / "cache",
            PAGES_DIR=base / "cache" / "pages",
            FILES_DIR=base / "files",
            EXPORT_DIR=base / "exports",
            FILM_DIR=base / "film",
            DB_PATH=base / "murmur.db",
        ):
            config.ensure_dirs()
            store.init()
            yield base


def pdf_bytes(paragraphs: list[str]) -> bytes:
    """A one-page PDF with a real text layer, assembled byte by byte."""
    lines = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
    for paragraph in paragraphs:
        escaped = (
            paragraph.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        )
        lines.append(f"({escaped}) Tj T*")
    lines.append("ET")
    content = "\n".join(lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content
        + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def epub_bytes() -> bytes:
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    package = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">urn:uuid:import-flow</dc:identifier>'
        "<dc:title>Import Flow Book</dc:title><dc:language>en</dc:language>"
        "</metadata>"
        '<manifest><item id="ch1" href="chapter1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="ch1"/></spine></package>'
    )
    chapter = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title>'
        "</head><body><h1>Chapter One</h1>"
        "<p>The first sentence of the chapter. A second sentence follows it.</p>"
        "</body></html>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter1.xhtml", chapter)
    return buffer.getvalue()


def docx_bytes() -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("Word Chapter", level=1)
    document.add_paragraph(
        "The first sentence from a Word file. A second sentence follows it."
    )
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


RTF = (
    b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times New Roman;}}"
    b"\\f0\\fs24 First RTF sentence. A second sentence follows it.\\par"
)


class ImportFlowTests(unittest.TestCase):
    """Every supported file type must import, and nothing may return a 500."""

    def _upload(self, client: TestClient, name: str, data: bytes, mime: str):
        return client.post(
            "/api/docs/upload", files={"file": (name, data, mime)}
        )

    def test_every_supported_file_type_imports(self) -> None:
        cases = {
            "chapter.pdf": (pdf_bytes(
                ["The first sentence of the page. A second sentence follows it.",
                 "A third sentence closes the paragraph."]
            ), "application/pdf"),
            "book.epub": (epub_bytes(), "application/epub+zip"),
            "notes.docx": (docx_bytes(),
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "notes.rtf": (RTF, "application/rtf"),
            "notes.txt": (b"Plain text speaks too. Here is another sentence.",
                          "text/plain"),
            "notes.md": (b"# Heading\n\nMarkdown body sentence. Another one here.",
                         "text/markdown"),
            "page.html": (
                b"<html><head><title>Page</title></head><body><p>"
                b"An extracted article sentence. And a second one.</p></body></html>",
                "text/html",
            ),
        }
        with temp_home():
            client = TestClient(server.app)
            for name, (data, mime) in cases.items():
                with self.subTest(name=name):
                    response = self._upload(client, name, data, mime)
                    self.assertEqual(
                        200, response.status_code, response.text[:400]
                    )
                    body = response.json()
                    self.assertGreaterEqual(body["unitCount"], 1, name)
                    self.assertTrue(body["title"], name)

    def test_imported_document_is_listed_and_readable(self) -> None:
        with temp_home():
            client = TestClient(server.app)
            created = self._upload(
                client,
                "chapter.pdf",
                pdf_bytes(["A sentence to read. And a second sentence to read."]),
                "application/pdf",
            ).json()

            listing = client.get("/api/docs").json()
            self.assertIn(created["id"], [item["id"] for item in listing["docs"]])
            document = client.get(f"/api/docs/{created['id']}").json()
            self.assertGreaterEqual(len(document["units"]), 1)
            self.assertIn("sentence", document["text"].lower())

    def test_image_without_ocr_backend_fails_cleanly(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (80, 40), (250, 250, 250)).save(buffer, format="PNG")

        with temp_home():
            client = TestClient(server.app)
            response = self._upload(client, "page.png", buffer.getvalue(), "image/png")
            if response.status_code == 200:  # an OCR backend is installed here
                self.assertGreaterEqual(response.json()["unitCount"], 1)
            else:
                self.assertIn(response.status_code, (400, 503), response.text[:400])

    def test_rejected_upload_does_not_report_a_server_error(self) -> None:
        with temp_home():
            client = TestClient(server.app)
            response = self._upload(client, "notes.xyz", b"nonsense", "application/octet-stream")
            self.assertIn(response.status_code, (400, 415), response.text[:400])

    def test_rtf_font_table_does_not_land_in_the_title(self) -> None:
        with temp_home():
            client = TestClient(server.app)
            response = self._upload(client, "notes.rtf", RTF, "application/rtf")
            self.assertEqual(200, response.status_code, response.text[:300])
            title = response.json()["title"]
            self.assertNotIn("Times New Roman", title)
            self.assertIn("RTF sentence", title)


if __name__ == "__main__":
    unittest.main()
