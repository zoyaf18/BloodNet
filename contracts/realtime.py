"""Transport-neutral case update subscription primitives."""

from __future__ import annotations

from collections import defaultdict
from queue import Queue
from typing import Any


class CaseRealtimeHub:
    """Local adapter; production can replace this with Firestore/Pub/Sub."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe_to_case(self, case_id: str) -> Queue[dict[str, Any]]:
        queue: Queue[dict[str, Any]] = Queue()
        self._subscribers[case_id].add(queue)
        return queue

    def unsubscribe_from_case(self, case_id: str, queue: Queue[dict[str, Any]]) -> None:
        self._subscribers[case_id].discard(queue)
        if not self._subscribers[case_id]:
            del self._subscribers[case_id]

    def publish_case(self, case_id: str, snapshot: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers.get(case_id, ())):
            queue.put(snapshot)


case_realtime_hub = CaseRealtimeHub()
