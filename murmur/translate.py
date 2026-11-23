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

from . import config, proc, store


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


def _saved_url() -> str:
    """The endpoint the reader connected in Settings or the first-run screen."""
    try:
        return str(store.get_settings().get("translateUrl") or "").strip()
    except Exception:  # pragma: no cover - a store that is not open yet
        return ""


def reset_discovery() -> None:
    """Forget what the port scan found (a server was just started, stopped or chosen)."""
    global _discovery
    _discovery = None


def probe_ports(timeout: float = 0.6) -> list[dict]:
    """Every usual local port, and what answered. No cache: this is an explicit question."""
    found: list[dict] = []
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
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
                model = str(models[0].get("id", "")) if models else ""
                found.append({"url": base, "model": model, "port": port,
                              "models": [str(m.get("id", "")) for m in models][:6]})
    except Exception:  # pragma: no cover - httpx missing
        return []
    return found


def check_url(url: str, timeout: float = 3.0) -> dict:
    """Does this endpoint answer like an OpenAI-compatible server? Says why if not."""
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return {"ok": False, "reason": "Give the address of a local model server."}
    if not cleaned.startswith(("http://127.0.0.1", "http://localhost")):
        return {"ok": False, "reason": "Use a server on this machine: http://127.0.0.1:<port>/v1."}
    # Accept what a person is likely to paste: a base, a base with /v1, or the full
    # chat-completions URL an OpenAI client config uses.
    for _ in range(3):
        for suffix in ("/chat/completions", "/completions", "/models", "/v1"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break
        else:
            break
    base = f"{cleaned}/v1"
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/models")
    except Exception as exc:
        return {"ok": False, "reason": f"Nothing answered at {base} ({exc.__class__.__name__})."}
    if response.status_code != 200:
        return {"ok": False, "reason": f"{base} answered {response.status_code}."}
    try:
        models = response.json().get("data") or []
    except Exception:
        return {"ok": False, "reason": f"{base} answered, but not with a model list."}
    model = str(models[0].get("id", "")) if models else ""
    return {"ok": True, "url": base, "model": model,
            "models": [str(m.get("id", "")) for m in models][:6]}


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
        self._process = proc.spawn(
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
        # Recompute rather than cache: the reader can connect or change a server while the
        # app is running, and a stale URL meant "available" for one that had gone away.
        # Order: what the reader connected, then the environment (a script's choice), then
        # whatever is listening - a UI choice must not be silently overridden by an old
        # MURMUR_TRANSLATE_URL left in a shell.
        saved = _saved_url()
        if saved:
            self.url = saved
            return True
        if self.configured:
            self.url = self.configured
            return True
        self.url = ""
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

    def status(self, probe: bool = False) -> dict:
        """What translation can do here, in the terms a person needs to act on.

        `probe` scans the usual ports now; otherwise the 30-second discovery cache answers,
        which is what a page-load poll can afford.
        """
        backend = self.backend()
        saved = _saved_url()
        target = self.http.url if backend == self.http.name else saved
        servers = probe_ports() if probe else [
            {"url": item, "model": model}
            for item, model in ([_discover()] if _discover() else [])
        ]
        # A saved endpoint is a promise; check it now rather than at the first foreign
        # sentence, because that is the whole point of asking before it is needed.
        if backend == self.http.name and target:
            checked = check_url(target, timeout=1.5)
            if not checked["ok"]:
                backend = None
                reason = f"{checked['reason']} Start it again, or pick another address."
                return {
                    "available": False,
                    "backend": None,
                    "apple": self.swift.available(),
                    "configuredUrl": target,
                    "savedUrl": saved,
                    "servers": servers,
                    "reason": reason,
                    "ports": list(LOCAL_PORTS),
                }
        reason = ""
        if backend is None:
            reason = (
                "Translation needs a local model server. Rhapsode uses one you already run: "
                "llama.cpp, LM Studio, Ollama or vLLM, on any of "
                + ", ".join(str(port) for port in LOCAL_PORTS)
                + "."
            )
        return {
            "available": backend is not None,
            "backend": backend,
            "apple": self.swift.available(),
            "configuredUrl": target or "",
            "savedUrl": saved,
            "servers": servers,
            "reason": reason,
            "ports": list(LOCAL_PORTS),
        }

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
