"""Small in-memory idempotency store for local development.

The interface is intentionally tiny so it can later be backed by Firestore or
another durable store without changing event handlers.
"""
from __future__ import annotations

from threading import Lock


class IdempotencyStore:
    def __init__(self) -> None:
        self._processed: set[str] = set()
        self._lock = Lock()

    def claim(self, event_id: str) -> bool:
        """Return True exactly once for each event_id."""
        with self._lock:
            if event_id in self._processed:
                return False
            self._processed.add(event_id)
            return True

    def contains(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._processed

    def clear(self) -> None:
        with self._lock:
            self._processed.clear()
