"""In-memory and Pub/Sub-compatible event bus adapters for local development.

The interface mirrors the operations needed by the future GCP Pub/Sub adapter.
Handlers receive a validated EventEnvelope and may return another envelope.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from contracts.events import EventEnvelope, EventType
from contracts.idempotency import IdempotencyStore
from contracts.observability import configure_logging, log_event

HandlerResult = EventEnvelope[Any] | list[EventEnvelope[Any]] | None
Handler = Callable[[EventEnvelope[Any]], HandlerResult]


@dataclass
class DeadLetterRecord:
    event: EventEnvelope[Any]
    error: str
    handler: str | None = None
    received_at: str | None = None

    @property
    def event_id(self) -> str:
        return self.event.event_id

    @property
    def event_type(self) -> str:
        return self.event.event_type.value

    @property
    def request_id(self) -> str:
        return self.event.request_id

    @property
    def correlation_id(self) -> str:
        return self.event.correlation_id


def _serialize_event(event: EventEnvelope[Any]) -> bytes:
    """Serialize the Pub/Sub data field as UTF-8 encoded JSON."""
    return json.dumps(
        event.model_dump(mode="json"), separators=(",", ":")
    ).encode("utf-8")


class DurableEventQueue:
    """SQLite-backed replay queue for cross-instance event durability."""

    def __init__(self, path: str = "bloodnet_event_queue.sqlite") -> None:
        self.path = path
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_queue (
                event_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_error TEXT
            )
            """
        )
        self._conn.commit()

    def enqueue(self, event: EventEnvelope[Any]) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO event_queue (event_id, payload, state, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (event.event_id, json.dumps(event.model_dump(mode="json"), separators=(",", ":")), datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def pending(self, limit: int | None = None) -> list[EventEnvelope[Any]]:
        rows = self._conn.execute(
            "SELECT payload FROM event_queue WHERE state = 'pending' ORDER BY created_at ASC LIMIT ?",
            ((limit or 1000),),
        ).fetchall()
        return [EventEnvelope.model_validate_json(row[0]) for row in rows]

    def mark_processing(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE event_queue SET state = 'processing', updated_at = ? WHERE event_id = ?",
            (datetime.now(timezone.utc).isoformat(), event_id),
        )
        self._conn.commit()

    def mark_completed(self, event_id: str) -> None:
        self._conn.execute(
            "UPDATE event_queue SET state = 'completed', updated_at = ? WHERE event_id = ?",
            (datetime.now(timezone.utc).isoformat(), event_id),
        )
        self._conn.commit()

    def mark_failed(self, event_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE event_queue SET state = 'failed', last_error = ?, updated_at = ? WHERE event_id = ?",
            (error, datetime.now(timezone.utc).isoformat(), event_id),
        )
        self._conn.commit()

    def replay(self, limit: int | None = None) -> list[EventEnvelope[Any]]:
        return self.pending(limit=limit)

    def close(self) -> None:
        self._conn.close()


class ConsumerOffsetStore:
    """SQLite-backed offset tracking so each consumer subscribes independently."""

    def __init__(self, path: str = "bloodnet_consumer_offsets.sqlite") -> None:
        self.path = path
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consumer_offsets (
                consumer_name TEXT NOT NULL,
                subscription TEXT NOT NULL,
                offset INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (consumer_name, subscription)
            )
            """
        )
        self._conn.commit()

    def record(self, consumer_name: str, subscription: str, offset: int) -> None:
        self._conn.execute(
            """
            INSERT INTO consumer_offsets (consumer_name, subscription, offset, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(consumer_name, subscription)
            DO UPDATE SET offset = excluded.offset, updated_at = excluded.updated_at
            """,
            (consumer_name, subscription, offset, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get(self, consumer_name: str, subscription: str) -> int | None:
        row = self._conn.execute(
            "SELECT offset FROM consumer_offsets WHERE consumer_name = ? AND subscription = ?",
            (consumer_name, subscription),
        ).fetchone()
        return None if row is None else int(row[0])

    def close(self) -> None:
        self._conn.close()


class LocalEventBus:
    def __init__(self, *, idempotency: IdempotencyStore | None = None) -> None:
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)
        self.idempotency = idempotency or IdempotencyStore()
        self.logger = configure_logging()
        self.dead_letter: list[DeadLetterRecord] = []
        self.queue = DurableEventQueue()
        self.offsets = ConsumerOffsetStore()

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def _record_dead_letter(self, event: EventEnvelope[Any], error: Exception, handler: Handler | None = None) -> None:
        entry = DeadLetterRecord(
            event=event,
            error=str(error),
            handler=getattr(handler, "__qualname__", repr(handler)),
            received_at=event.occurred_at.isoformat(),
        )
        self.dead_letter.append(entry)
        log_event(
            self.logger,
            "event_dead_lettered",
            event_id=event.event_id,
            event_type=event.event_type.value,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
            error=str(error),
        )

    def replay_dead_letters(self, *, limit: int | None = None) -> list[EventEnvelope[Any]]:
        queued = list(self.dead_letter)
        if limit is not None:
            queued = queued[:limit]
        return [item.event for item in queued]

    def enqueue_for_replay(self, event: EventEnvelope[Any]) -> None:
        self.queue.enqueue(event)

    def publish(self, event: EventEnvelope[Any]) -> list[EventEnvelope[Any]]:
        if not self.idempotency.claim(event.event_id):
            log_event(
                self.logger,
                "duplicate_event_ignored",
                event_id=event.event_id,
                event_type=event.event_type.value,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
            )
            return []

        log_event(
            self.logger,
            "event_published",
            event_id=event.event_id,
            event_type=event.event_type.value,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
        )

        outputs: list[EventEnvelope[Any]] = []
        for handler in self._handlers.get(event.event_type, []):
            try:
                output = handler(event)
            except Exception as exc:  # pragma: no cover - same path exercised by tests
                self._record_dead_letter(event, exc, handler=handler)
                continue
            if isinstance(output, list):
                outputs.extend(output)
            elif output is not None:
                outputs.append(output)
        return outputs

    def publish_external(self, event: EventEnvelope[Any]) -> None:
        """Publish an envelope to Pub/Sub without invoking local consumers."""
        payload = _serialize_event(event)
        publish_future = self.publisher.publish(
            self._topic_path(),
            payload,
            event_id=event.event_id,
            event_type=event.event_type.value,
            schema_version=event.schema_version,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
        )
        result = getattr(publish_future, "result", None)
        if callable(result):
            result()


class PubSubEventBus(LocalEventBus):
    """Pub/Sub transport adapter with the same local handler semantics.

    A Google publisher is created lazily when one is not injected, so importing
    the contracts package remains possible in local environments without the
    cloud dependency or credentials.
    """

    def __init__(
        self,
        *,
        publisher: Any | None = None,
        topic: str = "bloodnet-events",
        project_id: str | None = None,
        idempotency: IdempotencyStore | None = None,
    ) -> None:
        super().__init__(idempotency=idempotency)
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT")
        self.publisher = publisher or self._create_publisher()
        self.topic = topic

    def _create_publisher(self) -> Any:
        if not self.project_id:
            raise ValueError(
                "project_id or GOOGLE_CLOUD_PROJECT is required for Pub/Sub transport"
            )

        try:
            from google.cloud import pubsub_v1
        except ImportError as exc:
            raise RuntimeError(
                "Pub/Sub transport requires the 'google-cloud-pubsub' package"
            ) from exc
        return pubsub_v1.PublisherClient()

    def _topic_path(self) -> str:
        if self.topic.startswith("projects/"):
            return self.topic
        if self.project_id and hasattr(self.publisher, "topic_path"):
            return self.publisher.topic_path(self.project_id, self.topic)
        return self.topic

    def publish(self, event: EventEnvelope[Any]) -> list[EventEnvelope[Any]]:
        if not self.idempotency.claim(event.event_id):
            log_event(
                self.logger,
                "duplicate_event_ignored",
                event_id=event.event_id,
                event_type=event.event_type.value,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
            )
            return []

        payload = _serialize_event(event)
        
        self.logger.debug(
            "PubSubEventBus.publish: payload_size=%d, payload_type=%s, payload_start=%r",
            len(payload),
            type(payload).__name__,
            payload[:50],
        )
        
        publish_future = self.publisher.publish(
            self._topic_path(),
            payload,
            event_id=event.event_id,
            event_type=event.event_type.value,
            schema_version=event.schema_version,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
        )
        result = getattr(publish_future, "result", None)
        if callable(result):
            result()

        log_event(
            self.logger,
            "event_published",
            event_id=event.event_id,
            event_type=event.event_type.value,
            request_id=event.request_id,
            correlation_id=event.correlation_id,
        )

        outputs: list[EventEnvelope[Any]] = []
        for handler in self._handlers.get(event.event_type, []):
            output = handler(event)
            if isinstance(output, list):
                outputs.extend(output)
            elif output is not None:
                outputs.append(output)
        return outputs


def create_event_bus(
    *,
    transport: str | None = None,
    project_id: str | None = None,
    topic: str | None = None,
    idempotency: IdempotencyStore | None = None,
) -> LocalEventBus:
    """Create the configured transport; local remains the default."""
    selected_transport = (transport or os.getenv("BLOODNET_EVENT_TRANSPORT", "local")).lower()
    if selected_transport == "local":
        return LocalEventBus(idempotency=idempotency)
    if selected_transport == "pubsub":
        return PubSubEventBus(
            project_id=project_id,
            topic=topic or os.getenv("BLOODNET_PUBSUB_TOPIC", "bloodnet-events"),
            idempotency=idempotency,
        )
    raise ValueError(f"Unsupported event transport: {selected_transport}")
