"""Download a file to disk, atomically, with progress the UI can draw.

Two things in the app fetch large files: the Kokoro voice (bootstrap) and the
on-device translator (localmt). Both want the same contract - write to `.part`,
report bytes as they arrive, fail loudly instead of hanging, and never leave a
half-written file where the app will treat it as complete.
"""
from __future__ import annotations

import os
import time
import urllib.request
from pathlib import Path
from typing import Callable

#: Called as progress(downloaded_bytes, total_bytes, megabytes_per_second).
#: `total` is 0 when the server sends no Content-Length.
Progress = Callable[[int, int, float], None]


class DownloadError(Exception):
    """A download that did not complete, with something a person can read."""


def fetch(url: str, dest: Path, *, progress: Progress | None = None,
          timeout: float = 60.0, chunk: int = 256 * 1024,
          sha256: str | None = None) -> Path:
    """Download `url` to `dest` through `dest.part`, then rename it into place.

    `timeout` is per socket operation: a stalled connection raises instead of holding
    a first-run screen open forever, which `urlretrieve` (no timeout) did. When
    `sha256` is given, a file that does not match is deleted and refused - the caller
    pins the weights it ships, so a truncated or substituted file must never be used.
    """
    import hashlib

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    started = time.time()
    downloaded = 0
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, open(tmp, "wb") as handle:
            total = int(response.headers.get("content-length") or 0)
            while True:
                block = response.read(chunk)
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                downloaded += len(block)
                if progress is not None:
                    elapsed = max(time.time() - started, 0.001)
                    progress(downloaded, total, downloaded / elapsed / 1e6)
        if downloaded == 0:
            raise DownloadError(f"{url} answered with no data")
        if sha256 and digest.hexdigest().lower() != sha256.lower():
            raise DownloadError(
                f"{dest.name} did not match its checksum "
                f"({digest.hexdigest()[:12]}...) - the download is damaged or the file changed"
            )
        os.replace(tmp, dest)
        return dest
    except DownloadError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - the caller decides what to say
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"{url}: {exc}") from exc
