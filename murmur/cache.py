"""A tiny in-memory LRU, so repeated lookups never touch the disk.

Keys here are content hashes (a synthesised clip, a translated sentence), not dense
integers — that makes a dictionary the right structure: a dict lookup is O(1) and
an array of hashes would only add a search. The array shape belongs to the places
where the index really is a scene number, and those already use lists.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable


class LRUCache:
    """Bounded mapping with least-recently-used eviction and hit counters."""

    def __init__(self, maxsize: int = 512, name: str = "cache") -> None:
        self.maxsize = max(1, int(maxsize))
        self.name = name
        self._items: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._items:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return self._items[key]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)

    def get_or_load(self, key: str, loader: Callable[[], Any]) -> Any | None:
        """Return the cached value, calling `loader` on a miss (None is not cached)."""
        cached = self.get(key)
        if cached is not None:
            return cached
        loaded = loader()
        if loaded is not None:
            self.put(key, loaded)
        return loaded

    def discard(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "name": self.name,
                "entries": len(self._items),
                "maxsize": self.maxsize,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
            }
