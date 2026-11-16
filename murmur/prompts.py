"""On-device prompt writing with Apple's Foundation Models."""
from __future__ import annotations

import json
import os
import select
import subprocess
import threading
from pathlib import Path

from . import config


class PromptUnavailable(Exception):
    pass


class PromptError(Exception):
    pass


class PromptWriter:
    def __init__(self, binary: Path | None = None) -> None:
        self.binary = Path(binary) if binary else config.PROMPTS_BIN
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0

    def available(self) -> bool:
        try:
            return self.binary.exists() and os.access(self.binary, os.X_OK)
        except OSError:
            return False

    def _start(self) -> None:
        if not self.available():
            raise PromptUnavailable("The prompt writer is not installed.")
        self._process = subprocess.Popen(
            [str(self.binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def _request(self, payload: dict) -> str:
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._start()
            self._next_id += 1
            request_id = self._next_id
            payload["id"] = request_id
            line = json.dumps(payload, ensure_ascii=False)

            try:
                assert self._process and self._process.stdin and self._process.stdout
                self._process.stdin.write(line + "\n")
                self._process.stdin.flush()
                ready, _, _ = select.select([self._process.stdout], [], [], 90)
                if not ready:
                    self._process.kill()
                    self._process = None
                    raise PromptError("Prompt writing timed out")
                data = json.loads(self._process.stdout.readline())
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._process = None
                raise PromptError(f"Prompt writing failed: {exc}") from exc

            if data.get("error"):
                raise PromptError(str(data["error"]))
            text = str(data.get("text") or "").strip()
            if not text:
                raise PromptError("The prompt writer returned nothing")
            return text

    def bible(self, sample: str) -> str:
        return self._request({"op": "bible", "text": sample})

    def cast(self, sample: str) -> str:
        return self._request({"op": "cast", "text": sample})

    def prompt(self, text: str, before: str = "", after: str = "", bible: str = "",
               shot: str = "") -> str:
        return self._request(
            {
                "op": "prompt",
                "text": text,
                "before": before,
                "after": after,
                "bible": bible,
                "shot": shot,
            }
        )


writer = PromptWriter()
