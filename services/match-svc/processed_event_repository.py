"""Durable event-consumption state for fulfillment handlers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os

import psycopg


class ProcessedEventRepository:
    """PostgreSQL-backed atomic event claim repository."""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.environ["BLOODNET_DATABASE_URL"]

    def try_claim(
        self,
        event_id: str,
        event_type: str,
        *,
        connection: psycopg.Connection | None = None,
    ) -> bool:
        if connection is not None:
            return self._try_claim_on(connection, event_id, event_type)

        with psycopg.connect(self.database_url) as owned_connection:
            return self._try_claim_on(owned_connection, event_id, event_type)

    @staticmethod
    def _try_claim_on(
        connection: psycopg.Connection,
        event_id: str,
        event_type: str,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT INTO processed_events (event_id, event_type)
            VALUES (%s, %s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (event_id, event_type),
        )
        return cursor.rowcount == 1


class InMemoryProcessedEventRepository:
    """Compatibility implementation for the local event-flow simulator."""

    def __init__(self) -> None:
        self._event_ids: set[str] = set()

    def try_claim(
        self,
        event_id: str,
        event_type: str,
        *,
        connection: object | None = None,
    ) -> bool:
        if event_id in self._event_ids:
            return False
        self._event_ids.add(event_id)
        return True