"""Sentence embeddings for library search: all-MiniLM-L6-v2 as ONNX, on the CPU.

Semantic search is optional. Lexical search (SQLite FTS5) needs nothing beyond
the standard library, and this module is only imported and only touches the
network when the user asks for the model by pressing "download" in the search
panel. Nothing here runs at startup, and nothing here needs a GPU, PyTorch, or
a cloud service.

The weights are the Apache-2.0 `Xenova/all-MiniLM-L6-v2` export (the int8
quantized ONNX file, ~23 MB) and the tokenizer that ships beside it. They live
in the same models directory as Kokoro, under `minilm-l6-v2/`, so uninstalling
Rhapsode leaves one directory to delete. `murmur.bootstrap` does **not** fetch
this model: reading works without it.

Set `MURMUR_SEMANTIC=off` to make the app ignore it even when the files exist.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import urllib.request
from pathlib import Path

import numpy as np

from . import config

MODEL_ID = "Xenova/all-MiniLM-L6-v2"
MODEL_NAME = "all-MiniLM-L6-v2"
MODEL_LICENSE = "Apache-2.0"
DIM = 384
MAX_TOKENS = 256
BATCH = 32

_BASE_URL = f"https://huggingface.co/{MODEL_ID}/resolve/main"
# Local file name -> (remote path, expected bytes). The expected size is only
# used to report download progress; a different size does not abort the fetch.
FILES = {
    "model.onnx": ("onnx/model_quantized.onnx", 22_972_370),
    "tokenizer.json": ("tokenizer.json", 711_661),
}
META_FILE = "model.json"

# MiniLM's BERT-style special ids. Read from the tokenizer when present; these
# are the values the shipped vocabulary uses and the fallback if it is odd.
CLS_ID, SEP_ID, PAD_ID, UNK_ID = 101, 102, 0, 100

_lock = threading.RLock()
_session = None
_tokenizer = None
_load_error = ""
_download = {"state": "idle", "progress": 0.0, "error": "", "thread": None}


def enabled() -> bool:
    """Whether semantic search is switched on at all (the files are separate)."""
    return os.environ.get("MURMUR_SEMANTIC", "").strip().lower() not in {
        "0", "off", "false", "no", "disabled",
    }


def folder() -> Path:
    """Where the model lives. Derived per call so tests can move the data dir."""
    return Path(config.MODELS_DIR) / "minilm-l6-v2"


def _file_paths() -> dict[str, Path]:
    base = folder()
    return {name: base / name for name in FILES}


def files_present() -> bool:
    return all(path.is_file() and path.stat().st_size > 0 for path in _file_paths().values())


def available() -> bool:
    """True when embeddings can be produced without downloading anything.

    This is a file check, not a load: a corrupt file shows up as an error from
    :func:`embed`, which the search layer downgrades to lexical-only.
    """
    return enabled() and files_present()


def status() -> dict:
    with _lock:
        paths = _file_paths()
        present = all(p.is_file() and p.stat().st_size > 0 for p in paths.values())
        if not enabled():
            state = "off"
        elif _download["state"] == "downloading":
            state = "downloading"
        elif _download["state"] == "error":
            state = "error"
        elif present:
            state = "ready" if _session is not None or not _load_error else "error"
        else:
            state = "missing"
        total = sum(p.stat().st_size for p in paths.values() if p.is_file())
        return {
            "available": bool(enabled() and present and not _load_error),
            "enabled": enabled(),
            "state": state,
            "model": MODEL_NAME,
            "license": MODEL_LICENSE,
            "dim": DIM,
            "path": str(folder()),
            "bytes": total,
            "expectedBytes": sum(size for _, size in FILES.values()),
            "progress": 1.0 if present else round(float(_download["progress"]), 3),
            "error": _download["error"] or _load_error,
        }


# ------------------------------------------------------------------ download
def start_download() -> dict:
    """Fetch the weights in the background. Idempotent; never blocks the caller."""
    if not enabled():
        raise RuntimeError("Semantic search is disabled (MURMUR_SEMANTIC=off).")
    with _lock:
        if files_present():
            _download["state"] = "idle"
            return status()
        thread = _download.get("thread")
        if thread is not None and thread.is_alive():
            return status()
        _download.update(state="downloading", progress=0.0, error="")
        thread = threading.Thread(target=_download_files, name="search-model", daemon=True)
        _download["thread"] = thread
        thread.start()
    return status()


def _download_files() -> None:
    """Stream each file to `<name>.part`, then rename it into place.

    A half-written file can never be mistaken for a usable model, and a crash
    mid-download leaves only a `.part` file behind.
    """
    paths = _file_paths()
    base = folder()
    expected = sum(size for _, size in FILES.values())
    done = 0
    try:
        base.mkdir(parents=True, exist_ok=True)
        for name, (remote, size) in FILES.items():
            target = paths[name]
            if target.is_file() and target.stat().st_size > 0:
                done += target.stat().st_size
                continue
            url = f"{_BASE_URL}/{remote}"
            part = target.with_name(target.name + ".part")
            request = urllib.request.Request(url, headers={"User-Agent": "Rhapsode-search"})
            written = 0
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (fixed host)
                with part.open("wb") as handle:
                    while True:
                        block = response.read(256 * 1024)
                        if not block:
                            break
                        handle.write(block)
                        written += len(block)
                        with _lock:
                            _download["progress"] = min(0.99, (done + written) / expected)
            if written == 0:
                raise OSError(f"{remote} came back empty")
            part.replace(target)
            done += written
        (base / META_FILE).write_text(
            json.dumps(
                {
                    "id": MODEL_ID,
                    "file": "model.onnx",
                    "format": "onnx int8 (onnxruntime, CPU)",
                    "license": MODEL_LICENSE,
                    "files": [remote for remote, _ in FILES.values()],
                    "url": _BASE_URL,
                    "downloaded": time.time(),
                },
                indent=2,
            ),
            "utf-8",
        )
        with _lock:
            _download.update(state="idle", progress=1.0, error="")
    except Exception as exc:  # network, disk, HTTP status
        for path in paths.values():
            path.with_name(path.name + ".part").unlink(missing_ok=True)
        with _lock:
            _download.update(state="error", error=f"Model download failed: {exc}")


# ----------------------------------------------------------------- tokenizer
def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _is_control(char: str) -> bool:
    code = ord(char)
    if code in (0, 0xFFFD):
        return True
    if code == 0x09 or code == 0x0A or code == 0x0D:
        return False
    return unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs"}


def _is_punctuation(char: str) -> bool:
    code = ord(char)
    if 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126:
        return True
    return unicodedata.category(char).startswith("P")


class Tokenizer:
    """The WordPiece tokenizer from `tokenizer.json`, without `tokenizers`.

    Mirrors `BertNormalizer` + `BertPreTokenizer` + greedy longest-match-first
    WordPiece, which is what the model was trained with. It is deliberately the
    only piece of the pipeline reimplemented here: adding the `tokenizers`
    wheel for one 700 KB vocabulary would be a larger dependency than the whole
    search feature.
    """

    def __init__(self, path: Path, max_length: int | None = None) -> None:
        data = json.loads(path.read_text("utf-8"))
        model = data.get("model") or {}
        self.vocab: dict[str, int] = model.get("vocab") or {}
        self.unk = model.get("unk_token", "[UNK]")
        self.prefix = model.get("continuing_subword_prefix", "##")
        self.max_chars = int(model.get("max_input_chars_per_word", 100))
        # tokenizer.json's `truncation` is a pipeline default (128 tokens here),
        # not the model's limit: MiniLM was trained on 256, and a 600-character
        # chunk is ~150 tokens, so truncating at 128 would drop its tail.
        self.max_length = int(max_length or MAX_TOKENS)
        self.cls = int(self.vocab.get("[CLS]", CLS_ID))
        self.sep = int(self.vocab.get("[SEP]", SEP_ID))
        self.pad = int(self.vocab.get("[PAD]", PAD_ID))
        self.unk_id = int(self.vocab.get(self.unk, UNK_ID))

    # -- text normalisation ------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        chars = []
        for char in unicodedata.normalize("NFC", text):
            if _is_control(char):
                continue
            chars.append(" " if char.isspace() else char)
        cleaned = "".join(chars)
        # handle_chinese_chars: one token per CJK character.
        spaced = []
        for char in cleaned:
            if _is_cjk(char):
                spaced.extend((" ", char, " "))
            else:
                spaced.append(char)
        cleaned = "".join(spaced)
        # lowercase: true, strip_accents: null (tokenizer.json) means "follow
        # lowercase", i.e. lowercase and strip combining marks.
        cleaned = cleaned.lower()
        decomposed = unicodedata.normalize("NFD", cleaned)
        return unicodedata.normalize("NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))

    @staticmethod
    def _pretokenize(text: str) -> list[str]:
        tokens: list[str] = []
        for word in text.split():
            current: list[str] = []
            for char in word:
                if _is_punctuation(char):
                    if current:
                        tokens.append("".join(current))
                        current = []
                    tokens.append(char)
                else:
                    current.append(char)
            if current:
                tokens.append("".join(current))
        return tokens

    def _wordpiece(self, token: str) -> list[int]:
        if len(token) > self.max_chars:
            return [self.unk_id]
        pieces: list[int] = []
        start = 0
        while start < len(token):
            end = len(token)
            found = None
            while start < end:
                piece = token[start:end]
                if start > 0:
                    piece = self.prefix + piece
                if piece in self.vocab:
                    found = self.vocab[piece]
                    break
                end -= 1
            if found is None:
                return [self.unk_id]
            pieces.append(found)
            start = end
        return pieces

    # -- encoding ----------------------------------------------------------
    def encode(self, text: str) -> list[int]:
        ids: list[int] = []
        for token in self._pretokenize(self._normalize(text or "")):
            ids.extend(self._wordpiece(token))
            if len(ids) >= self.max_length - 2:
                break
        return [self.cls] + ids[: self.max_length - 2] + [self.sep]

    def encode_batch(self, texts: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        encoded = [self.encode(text) for text in texts]
        width = max((len(ids) for ids in encoded), default=2)
        input_ids = np.zeros((len(encoded), width), dtype=np.int64)
        mask = np.zeros((len(encoded), width), dtype=np.int64)
        for row, ids in enumerate(encoded):
            input_ids[row, : len(ids)] = ids
            mask[row, : len(ids)] = 1
        segments = np.zeros_like(input_ids)
        return input_ids, mask, segments


def _load() -> tuple[object, Tokenizer]:
    """Load the ONNX session and tokenizer once (thread-safe)."""
    global _session, _tokenizer, _load_error
    with _lock:
        if _session is not None and _tokenizer is not None:
            return _session, _tokenizer
        if not files_present():
            raise RuntimeError("The search model is not downloaded yet.")
        try:
            import onnxruntime as ort  # imported lazily: not a test dependency

            paths = _file_paths()
            options = ort.SessionOptions()
            # One thread: a query or an indexing batch must not fight the reader
            # for every core on an already-busy machine.
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(paths["model.onnx"]),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            tokenizer = Tokenizer(paths["tokenizer.json"])
        except Exception as exc:
            _load_error = f"The search model could not be loaded: {exc}"
            raise RuntimeError(_load_error) from exc
        _session, _tokenizer, _load_error = session, tokenizer, ""
        return _session, _tokenizer


def embed(texts: list[str]) -> np.ndarray:
    """Return L2-normalised float32 vectors, one row per text.

    The model exposes `last_hidden_state` only; mean pooling over the attention
    mask (then normalising) is the standard sentence-transformers recipe and
    makes the dot product equal the cosine similarity.
    """
    if not texts:
        return np.zeros((0, DIM), dtype=np.float32)
    session, tokenizer = _load()
    outputs: list[np.ndarray] = []
    for start in range(0, len(texts), BATCH):
        batch = texts[start : start + BATCH]
        input_ids, mask, segments = tokenizer.encode_batch(batch)
        feeds = {
            "input_ids": input_ids,
            "attention_mask": mask,
            "token_type_ids": segments,
        }
        names = {item.name for item in session.get_inputs()}
        feeds = {name: value for name, value in feeds.items() if name in names}
        hidden = session.run(None, feeds)[0]
        hidden = np.asarray(hidden, dtype=np.float32)
        if hidden.ndim == 3:
            weights = mask.astype(np.float32)[:, :, None]
            pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-6)
        else:
            pooled = hidden
        outputs.append(pooled.astype(np.float32))
    matrix = np.vstack(outputs) if outputs else np.zeros((0, DIM), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-9)


def reset() -> None:
    """Drop the loaded session (tests, and after a download finishes)."""
    global _session, _tokenizer, _load_error
    with _lock:
        _session, _tokenizer, _load_error = None, None, ""
