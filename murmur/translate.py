"""Translate sentences with the bundled Swift worker (Apple Translation)."""
from __future__ import annotations

import json
import os
import platform
import select
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config, proc


class TranslateUnavailable(Exception):
    pass


class TranslateError(Exception):
    pass


# Names for the languages the voice catalogue offers, for prompt writing.
LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "pt": "Portuguese",
    "hi": "Hindi",
    "ja": "Japanese",
    "zh": "Mandarin Chinese",
}

# Ports where a local OpenAI-compatible server usually listens on this kind of
# machine: llama-server, vLLM, LM Studio, Ollama.
LOCAL_PORTS = (8091, 8080, 8000, 1234, 11434)
_DISCOVERY_TTL = 30.0
_discovery: tuple[float, tuple[str, str] | None] | None = None


def _discover() -> tuple[str, str] | None:
    """First local server answering /v1/models, and the first model it offers."""
    global _discovery
    now = time.monotonic()
    if _discovery is not None and now - _discovery[0] < _DISCOVERY_TTL:
        return _discovery[1]

    found: tuple[str, str] | None = None
    try:
        import httpx

        with httpx.Client(timeout=0.3) as client:
            for port in LOCAL_PORTS:
                base = f"http://127.0.0.1:{port}/v1"
                try:
                    response = client.get(f"{base}/models")
                except Exception:
                    continue
                if response.status_code != 200:
                    continue
                try:
                    models = response.json().get("data") or []
                except Exception:
                    models = []
                model = str(models[0].get("id")) if models and isinstance(models[0], dict) else "local-model"
                found = (f"{base}/chat/completions", model)
                break
    except Exception:
        found = None

    _discovery = (now, found)
    return found


class Translator:
    name = "apple"

    def __init__(self, binary: Path | None = None) -> None:
        self.binary = Path(binary) if binary else config.TRANSLATE_BIN
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self.last_error: str | None = None

    def available(self) -> bool:
        try:
            return self.binary.exists() and os.access(self.binary, os.X_OK)
        except OSError:
            return False

    def _start(self) -> None:
        if not self.available():
            raise TranslateUnavailable(
                "Translation is unavailable — the murmur-translate helper is missing."
            )
        self._process = proc.popen(
            [str(self.binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def _ensure(self) -> None:
        if self._process is None or self._process.poll() is not None:
            self._start()

    def translate(self, text: str, target: str, source: str = "auto") -> dict:
        """Return {text, source, translated}. Raises TranslateUnavailable/Error."""
        with self._lock:
            self._ensure()
            self._next_id += 1
            request_id = self._next_id
            payload = json.dumps(
                {"id": request_id, "text": text, "source": source, "target": target},
                ensure_ascii=False,
            )

            try:
                assert self._process and self._process.stdin and self._process.stdout
                self._process.stdin.write(payload + "\n")
                self._process.stdin.flush()

                ready, _, _ = select.select([self._process.stdout], [], [], 25)
                if not ready:
                    self._process.kill()
                    self._process = None
                    raise TranslateError("Translation timed out")

                line = self._process.stdout.readline()
                data = json.loads(line)
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._process = None
                raise TranslateError(f"Translation failed: {exc}") from exc

            if data.get("id") != request_id:
                raise TranslateError("Translation worker returned a mismatched response")

            if data.get("error") == "not-installed":
                self.last_error = "language-pack"
                raise TranslateUnavailable(
                    "The language pack for that voice is not installed. Open System Settings → "
                    "General → Language & Region → Translation Languages and add it."
                )
            if data.get("error"):
                self.last_error = str(data["error"])
                raise TranslateError(str(data["error"]))

            return {
                "text": data.get("text", text),
                "source": data.get("source", ""),
                "translated": bool(data.get("translated")),
            }


class HttpTranslator:
    """Translate through an OpenAI-compatible endpoint on this machine.

    macOS has Apple's Translation framework behind the Swift worker. Everywhere
    else — and optionally on macOS too — point `MURMUR_TRANSLATE_URL` at a local
    server (llama-server, LM Studio, Ollama's /v1) and sentences are translated
    by a model that never leaves the machine. With no URL set, the usual local
    ports are probed, so a lane that is already running is picked up on its own.
    """

    name = "http"

    def __init__(self, url: str | None = None, model: str | None = None) -> None:
        self.configured = (url if url is not None else config.TRANSLATE_URL).rstrip("/")
        self.url = self.configured
        self.model = model or config.TRANSLATE_MODEL
        self.last_error: str | None = None

    def _resolve(self) -> bool:
        if self.url:
            return True
        found = _discover()
        if found is None:
            return False
        self.url, self.model = found
        return True

    def available(self) -> bool:
        return self._resolve()

    def _payload(self, text: str, target: str, source: str) -> dict:
        language = LANGUAGE_NAMES.get(target, target)
        instructions = (
            f"Translate the text the user sends into {language}. "
            "Keep the meaning, tone and paragraph breaks. "
            "Reply with the translation only: no notes, no explanations, no quotes."
        )
        if source and source not in {"auto", target}:
            instructions += f" The text is in {LANGUAGE_NAMES.get(source, source)}."
        return {
            "model": self.model,
            "temperature": 0,
            "stream": False,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": text},
            ],
        }

    def translate(self, text: str, target: str, source: str = "auto") -> dict:
        if not self._resolve():
            raise TranslateUnavailable(
                "No local translation server is running. Set MURMUR_TRANSLATE_URL, "
                "or start one (llama-server, LM Studio, Ollama)."
            )
        try:
            import httpx

            with httpx.Client(timeout=90.0) as client:
                response = client.post(self.url, json=self._payload(text, target, source))
            if response.status_code >= 400:
                detail = response.text.strip()[:200]
                raise TranslateError(f"Translation server said {response.status_code}: {detail}")
            data = response.json()
            translated = str(data["choices"][0]["message"]["content"] or "").strip()
        except TranslateError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            raise TranslateError(f"Translation failed: {exc}") from exc

        translated = translated.strip().strip('"').strip("“”").strip()
        if not translated:
            raise TranslateError("The translation server returned nothing.")
        return {
            "text": translated,
            "source": "" if source == "auto" else source,
            "translated": translated != text.strip(),
        }


def missing_advice() -> str:
    """What to tell the reader when nothing can translate.

    Off macOS the only route is a local OpenAI-compatible server, so pointing at
    ``MURMUR_TRANSLATE_URL`` is the right advice. On macOS the usual cause is
    different: the shipped .app was built without the Swift helper (it needs the
    macOS 26 SDK), or the Mac predates macOS 26 entirely - in both cases the
    environment variable advice is misleading and the reader cannot act on it.
    """
    if sys.platform != "darwin":
        return (
            "Translation is not set up on this machine, so the original text is being read. "
            "Point MURMUR_TRANSLATE_URL at a local model, or turn off Settings \u2192 "
            "Translate to voice language."
        )

    version = platform.mac_ver()[0] or ""
    try:
        major = int(version.split(".")[0])
    except ValueError:
        major = 0
    if major and major < 26:
        return (
            f"Apple Translation needs macOS 26 or newer; this Mac runs macOS {version}. "
            "The original text is being read - turn off Settings \u2192 Translate to voice "
            "language, or point MURMUR_TRANSLATE_URL at a local model."
        )
    return (
        "This build has no Apple translation helper, so the original text is being read. "
        "Rebuild on a Mac with the macOS 26 SDK, point MURMUR_TRANSLATE_URL at a local "
        "model, or turn off Settings \u2192 Translate to voice language."
    )


class TranslatorChain:
    """Apple Translation when it exists, a local model otherwise."""

    def __init__(self) -> None:
        self.swift = Translator()
        self.http = HttpTranslator()

    @property
    def last_error(self) -> str | None:
        return self.swift.last_error or self.http.last_error

    def available(self) -> bool:
        return self.swift.available() or self.http.available()

    def backend(self) -> str | None:
        if self.swift.available():
            return self.swift.name
        if self.http.available():
            return self.http.name
        return None

    def translate(self, text: str, target: str, source: str = "auto") -> dict:
        if self.swift.available():
            return self.swift.translate(text, target, source)
        if self.http.available():
            return self.http.translate(text, target, source)
        raise TranslateUnavailable(
            "Translation needs a local model on this machine: set "
            "MURMUR_TRANSLATE_URL to an OpenAI-compatible server, or start one on "
            "the usual ports."
        )


translator = TranslatorChain()
