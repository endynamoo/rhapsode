"""The on-device translator: llama.cpp's server plus one Apache-2.0 model.

A reader should not have to install llama.cpp, LM Studio or Ollama to hear a
foreign book. This module owns that side: it fetches the runtime and the weights
once (behind an explicit choice - they are hundreds of megabytes), starts
`llama-server` on loopback, and gives `translate.LocalTranslator` an
OpenAI-shaped endpoint. Nothing leaves the machine and nothing else needs
installing.

Model choice is a licence question as much as a quality one, measured on
2026-11-24: Qwen2.5-3B is the best translator of the small set but ships under the
Qwen Research Licence, so it is not an option. 7B (Apache-2.0) translates Spanish,
French, Japanese and Chinese acceptably at ~2 s a sentence; 1.5B (Apache-2.0) is a
third of the download, twice as fast, and visibly weaker in French. Hindi is not
translatable by either, which is why `translate` can route individual languages to
a server the reader runs instead (Settings -> Translation).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import config, download, proc, store

TAG = "b12676"
_RELEASES = f"https://github.com/ggml-org/llama.cpp/releases/download/{TAG}"
_HF = "https://huggingface.co/{repo}/resolve/main/{name}"
_LICENSE = "LICENSE"

#: Where the runtime and the weights live, under the data directory.
RUNTIME_ROOT_NAME = "localmt"

#: Every platform llama.cpp publishes a CPU build for, with the archive's size floor
#: and (where this machine could compute it) its checksum. Non-Windows archives are
#: checked by size only: no box here to hash them, and a truncated tar fails to extract.
ASSETS = {
    "win32-x86_64": (f"{_RELEASES}/llama-{TAG}-bin-win-cpu-x64.zip", 24_000_000,
                     "fdf78a3924fe92ccfb24af58544a6dccbc1feeb0587e77fca9ec3bef6737086c"),
    "darwin-arm64": (f"{_RELEASES}/llama-{TAG}-bin-macos-arm64.tar.gz", 15_000_000, ""),
    "darwin-x86_64": (f"{_RELEASES}/llama-{TAG}-bin-macos-x64.tar.gz", 11_000_000, ""),
    "linux-x86_64": (f"{_RELEASES}/llama-{TAG}-bin-ubuntu-x64.tar.gz", 22_000_000, ""),
    "linux-aarch64": (f"{_RELEASES}/llama-{TAG}-bin-linux-arm64.tar.gz", 23_000_000, ""),
}

#: The weights a reader can fetch. Sizes and checksums are the files this release
#: was measured against; a mismatch is refused rather than loaded.
#:
#: `languages` is the measured capability, not the language list of the model card:
#: on 0.6B..7B Qwen produced fluent nonsense in Hindi (see issue #150), so Hindi is
#: absent here and the chain refuses it rather than reading gibberish aloud.
VARIANTS: dict[str, dict] = {
    "7b": {
        "label": "7B",
        "repo": "Qwen/Qwen2.5-7B-Instruct-GGUF",
        "note": "correct for Spanish, French, Japanese and Chinese; about 2 s a sentence",
        "languages": ["en", "es", "fr", "it", "pt", "ja", "zh"],
        "files": [
            ("qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf", 3_993_201_344,
             "dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db"),
            ("qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf", 689_872_288,
             "539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a"),
        ],
    },
    "1.5b": {
        "label": "1.5B",
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "note": "a third of the download and twice as fast; weaker in French and Japanese",
        "languages": ["en", "es", "fr", "it", "pt", "ja", "zh"],
        "files": [
            ("qwen2.5-1.5b-instruct-q4_k_m.gguf", 1_117_320_736,
             "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"),
        ],
    },
}

DEFAULT_VARIANT = "7b"

_STATE = {"running": False, "done": False, "error": None, "variant": "",
          "label": "", "size": 0, "progress": None}
_lock = threading.Lock()
_download_thread: threading.Thread | None = None
_server = None            # proc.spawn handle
_server_url: str = ""
_server_log = None        # open file handle for llama-server's output


# ---------------------------------------------------------------- where things go
def runtime_root() -> Path:
    return config.MODELS_DIR / RUNTIME_ROOT_NAME


def runtime_dir() -> Path:
    return runtime_root() / f"llama-{TAG}"


def model_dir(variant: str) -> Path:
    return runtime_root() / "models" / variant


def model_paths(variant: str) -> list[Path]:
    return [model_dir(variant) / name for name, _size, _sha in VARIANTS[variant]["files"]]


def licence_path(variant: str) -> Path:
    return model_dir(variant) / _LICENSE


def binary_path() -> Path | None:
    name = "llama-server.exe" if sys.platform.startswith("win") else "llama-server"
    for candidate in runtime_dir().rglob(name):
        if candidate.is_file():
            return candidate
    return None


def platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "win32-x86_64"
    if sys.platform == "darwin":
        return "darwin-arm64" if machine in {"arm64", "aarch64"} else "darwin-x86_64"
    return "linux-x86_64" if machine in {"x86_64", "amd64"} else "linux-aarch64"


def enabled() -> bool:
    """The switch that keeps the whole feature off (tests, and a reader who said no)."""
    return os.environ.get("MURMUR_LOCAL_MT", "").strip().lower() not in {
        "0", "off", "false", "no", "disabled",
    }


def variant() -> str:
    try:
        chosen = str(store.get_settings().get("translateVariant") or "").strip().lower()
    except Exception:  # pragma: no cover - a store that is not open yet
        chosen = ""
    return chosen if chosen in VARIANTS else DEFAULT_VARIANT


def installed(name: str | None = None) -> bool:
    """Runtime and every shard present at the size we expect."""
    name = name or variant()
    if binary_path() is None:
        return False
    for path, (_file, size, _sha) in zip(model_paths(name), VARIANTS[name]["files"]):
        if not path.is_file() or path.stat().st_size != size:
            return False
    return True


def size_of(name: str) -> int:
    return sum(size for _file, size, _sha in VARIANTS[name]["files"])


def languages(name: str | None = None) -> list[str]:
    """The languages this variant translates well, as measured (see VARIANTS)."""
    return list(VARIANTS[name or variant()]["languages"])


def supports(lang: str, name: str | None = None) -> bool:
    """Whether the bundled model can translate into `lang` ("hi-IN" counts as "hi")."""
    base = (lang or "").split("-")[0].lower()
    return bool(base) and base in languages(name)


# ---------------------------------------------------------------- the download
def state() -> dict:
    with _lock:
        snapshot = dict(_STATE)
    snapshot["progress"] = dict(snapshot["progress"]) if snapshot["progress"] else None
    return snapshot


def _report(label: str, size: int, downloaded: int, total: int, speed: float,
            variant_name: str) -> None:
    total = total or size
    percent = round(downloaded / total * 100, 1) if total else 0.0
    with _lock:
        _STATE.update(size=size, variant=variant_name)
        _STATE["progress"] = {
            "label": label,
            "downloaded": downloaded,
            "total": total,
            "percent": percent,
            "mbPerSecond": round(speed, 1),
        }


def start_download(name: str | None = None) -> dict:
    """Fetch the runtime and the weights in the background. Idempotent while running."""
    global _download_thread
    name = (name or variant()).lower()
    if name not in VARIANTS:
        raise ValueError(f"unknown translation model {name!r}")
    with _lock:
        if _STATE["running"]:
            return state()
        _STATE.update(running=True, done=False, error=None, variant=name,
                      label="", size=size_of(name), progress=None)

    def _run() -> None:
        try:
            _fetch_runtime(name)
            _fetch_weights(name)
            with _lock:
                _STATE.update(running=False, done=True, error=None, progress=None)
        except Exception as exc:  # noqa: BLE001 - reported to the window
            with _lock:
                _STATE.update(running=False, done=False, error=str(exc), progress=None)

    _download_thread = threading.Thread(target=_run, name="localmt-download", daemon=True)
    _download_thread.start()
    return state()


def _fetch_runtime(name: str) -> None:
    if binary_path() is not None:
        return
    key = platform_key()
    if key not in ASSETS:
        raise RuntimeError(f"no llama.cpp build for this platform ({key})")
    url, floor, sha = ASSETS[key]
    archive = runtime_root() / ("llama.zip" if url.endswith(".zip") else "llama.tar.gz")
    size = size_of(name)

    def progress(downloaded: int, total: int, speed: float) -> None:
        _report("the translation engine", size, downloaded, total or 0, speed, name)

    download.fetch(url, archive, progress=progress, sha256=sha or None)
    if archive.stat().st_size < floor:
        archive.unlink(missing_ok=True)
        raise RuntimeError("the translation engine download was truncated; try again")
    runtime_dir().mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(runtime_dir())
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(runtime_dir())
    archive.unlink(missing_ok=True)
    binary = binary_path()
    if binary is None:
        raise RuntimeError("the translation engine archive had no llama-server in it")
    if sys.platform != "win32":
        binary.chmod(binary.stat().st_mode | 0o111)


def _fetch_weights(name: str) -> None:
    spec = VARIANTS[name]
    licence = licence_path(name)
    for path, (file_name, size, sha) in zip(model_paths(name), spec["files"]):
        if path.is_file() and path.stat().st_size == size:
            continue

        def progress(downloaded: int, total: int, speed: float,
                     _file=file_name, _size=size) -> None:
            _report(_file, _size, downloaded, total or 0, speed, name)

        download.fetch(_HF.format(repo=spec["repo"], name=file_name), path,
                       progress=progress, sha256=sha)
    if not licence.is_file():
        # Apache-2.0 section 4 wants the licence carried with the work; it sits next to
        # the weights so a reader can find who wrote them and under what terms.
        try:
            download.fetch(_HF.format(repo=spec["repo"], name=_LICENSE), licence)
        except Exception:  # noqa: BLE001 - a missing licence must not block the download
            pass


# ---------------------------------------------------------------- the server
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def running() -> bool:
    return _server is not None and _server.poll() is None


def endpoint() -> str:
    """The OpenAI-shaped base URL of the local server, or "" when it is not up."""
    return _server_url


def ensure_server(timeout: float = 90.0) -> str:
    """Start `llama-server` if needed and return its `/v1` URL. Raises on failure."""
    global _server, _server_url, _server_log
    with _lock:
        if running() and _server_url:
            return _server_url
        name = variant()
        if not installed(name):
            raise RuntimeError("the on-device translator is not installed")
        binary = binary_path()
        if binary is None:  # pragma: no cover - installed() already checked
            raise RuntimeError("llama-server is missing")
        port = _free_port()
        _server_log = open(runtime_root() / "llama-server.log", "a", encoding="utf-8")
        command = [
            str(binary), "--model", str(model_paths(name)[0]),
            "--host", "127.0.0.1", "--port", str(port),
            "--ctx-size", "2048", "--threads", str(max(2, (os.cpu_count() or 4) // 2)),
        ]
        _server = proc.spawn(command, stdout=_server_log, stderr=_server_log,
                             bufsize=1, encoding="utf-8", errors="replace")
        url = f"http://127.0.0.1:{port}/v1"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if _server.poll() is not None:
                raise RuntimeError(
                    "llama-server exited while starting; see localmt/llama-server.log")
            try:
                with urllib.request.urlopen(f"{url}/models", timeout=2) as response:
                    if response.status == 200:
                        _server_url = url
                        return url
            except Exception:  # noqa: BLE001 - not ready yet
                time.sleep(0.5)
        raise RuntimeError("llama-server did not answer within "
                           f"{int(timeout)} s (7B takes a few seconds to load)")


def stop() -> None:
    """Stop the server and close its log. Safe to call when nothing is running."""
    global _server, _server_url, _server_log
    with _lock:
        server, _server = _server, None
        _server_url = ""
    if server is not None and server.poll() is None:
        try:
            server.terminate()
            server.wait(timeout=5)
        except Exception:  # noqa: BLE001 - fall through to a hard kill
            try:
                server.kill()
            except Exception:  # noqa: BLE001 - already gone
                pass
    if _server_log is not None:
        try:
            _server_log.close()
        except OSError:
            pass
        _server_log = None


def status() -> dict:
    name = variant()
    sizes = {key: {"label": spec["label"], "bytes": size_of(key), "note": spec["note"],
                   "languages": list(spec["languages"])}
             for key, spec in VARIANTS.items()}
    return {
        "enabled": enabled(),
        "variant": name,
        "installed": installed(name) if enabled() else False,
        "runtime": binary_path() is not None,
        "languages": languages(name),
        "server": {"running": running(), "url": _server_url},
        "download": state(),
        "variants": sizes,
        "reason": "" if enabled() else "the on-device translator is switched off in this build",
    }
