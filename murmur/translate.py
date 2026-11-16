"""Translate sentences with the bundled Swift worker (Apple Translation)."""
from __future__ import annotations

import json
import os
import select
import subprocess
import threading
from pathlib import Path

from . import config, proc


class TranslateUnavailable(Exception):
    pass


class TranslateError(Exception):
    pass


class Translator:
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


translator = Translator()
