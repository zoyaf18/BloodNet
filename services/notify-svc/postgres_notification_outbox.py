"""Durable PostgreSQL outbox for notification delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.models import Notification


class PostgreSQLNotificationOutbox:
    """
    Durable PostgreSQL outbox with lease-based processing for crash recovery.
    
    Processing flow:
    1. pending -> processing (with lease_expires_at = now + timeout)
    2. If delivered before lease expires: processing -> delivered
    3. If lease expires before delivery: processing -> pending (automatic via recovery)
    
    This ensures at-least-once delivery with automatic recovery from worker crashes.
    """
    
    def __init__(self, database_url: str, lease_timeout_seconds: int = 300) -> None:
        """
        Initialize the outbox.
        
        Args:
            database_url: PostgreSQL connection string
            lease_timeout_seconds: How long a lease is valid (default 5 minutes)
        """
        self.database_url = database_url
        self.lease_timeout_seconds = lease_timeout_seconds
        self.max_attempts = 5

    def enqueue(
        self,
        event_id: str,
        notification: Notification,
        cursor: psycopg.Cursor | None = None,
        *,
        correlation_id: str | None = None,
        provider_idempotency_key: str | None = None,
    ) -> bool:
        """
        Enqueue a notification to the outbox.
        
        If cursor is provided, uses that cursor (within an existing transaction).
        Otherwise, creates a new connection.
        """
        if cursor is not None:
            # Use provided cursor (within transaction)
            return self._execute_enqueue(cursor, event_id, notification, correlation_id, provider_idempotency_key)
        else:
            # Create new connection
            with psycopg.connect(self.database_url) as connection:
                return self._execute_enqueue(connection.cursor(), event_id, notification, correlation_id, provider_idempotency_key)

    def _execute_enqueue(
        self,
        cursor: psycopg.Cursor,
        event_id: str,
        notification: Notification,
        correlation_id: str | None,
        provider_idempotency_key: str | None,
    ) -> bool:
        """Execute the enqueue insert using the provided cursor."""
        cursor.execute(
            """
            INSERT INTO notification_outbox (
                event_id, notification_id, correlation_id, request_id, case_id,
                provider_idempotency_key, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (notification_id) DO NOTHING
            """,
            (
                event_id,
                notification.notification_id,
                correlation_id or notification.case_id,
                notification.request_id,
                notification.case_id,
                provider_idempotency_key or notification.notification_id,
                Jsonb(notification.model_dump(mode="json")),
            ),
        )
        return cursor.rowcount == 1

    def claim_pending(self, limit: int = 100, event_id: str | None = None) -> list[tuple[str, Notification]]:
        """
        Claim pending notifications for processing with lease-based recovery.
        
        Atomically:
        1. Recovers any expired processing leases back to pending
        2. Selects pending notifications
        3. Marks them as processing
        4. Sets locked_at and lease_expires_at
        5. Increments attempts counter
        
        Returns a list of (event_id, notification) tuples.
        """
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending',
                    locked_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE status = 'processing'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < NOW()
                  AND (%s::text IS NULL OR event_id = %s)
                """,
                (event_id, event_id),
            )
            connection.commit()

            lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.lease_timeout_seconds)
            
            rows = connection.execute(
                """
                SELECT event_id, payload
                FROM notification_outbox
                                WHERE status = 'pending'
                                    AND available_at <= NOW()
                                    AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                                      AND terminal_failure = false
                                      AND (%s::text IS NULL OR event_id = %s)
                ORDER BY created_at, event_id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (event_id, event_id, limit),
            ).fetchall()
            
            for row in rows:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'processing',
                        attempts = attempts + 1,
                        locked_at = NOW(),
                        lease_expires_at = %s,
                        updated_at = NOW()
                    WHERE event_id = %s
                    """,
                    (lease_expires_at, row["event_id"]),
                )
            
            connection.commit()
            
            return [
                (row["event_id"], Notification.model_validate(row["payload"]))
                for row in rows
            ]

    def mark_delivered(
        self,
        event_id: str,
        *,
        provider_message_id: str | None = None,
        delivery_status: str = "accepted",
    ) -> None:
        """Mark a provider handoff complete and persist its delivery metadata."""
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'delivered',
                    provider_message_id = COALESCE(%s, provider_message_id),
                    delivery_status = %s,
                    delivered_at = NOW(),
                    locked_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE event_id = %s
                """,
                (provider_message_id, delivery_status, event_id),
            )
            connection.commit()

    def record_receipt(
        self,
        provider_message_id: str,
        delivery_status: str,
        failure_reason: str | None = None,
    ) -> None:
        """Persist a receipt idempotently and project it onto the outbox row."""
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                INSERT INTO notification_delivery_receipts (
                    provider_message_id, delivery_status, failure_reason
                ) VALUES (%s, %s, %s)
                ON CONFLICT (provider_message_id, delivery_status) DO NOTHING
                """,
                (provider_message_id, delivery_status, failure_reason),
            )
            connection.execute(
                """
                UPDATE notification_outbox
                SET delivery_status = %s,
                    delivery_failure_reason = %s,
                    delivered_at = CASE
                        WHEN LOWER(%s) IN ('delivered', 'success', 'completed')
                        THEN COALESCE(delivered_at, NOW()) ELSE delivered_at END,
                    updated_at = NOW()
                WHERE provider_message_id = %s
                """,
                (delivery_status, failure_reason, delivery_status, provider_message_id),
            )

    def delivery_operations(self, limit: int = 100) -> list[dict]:
        """Return non-PII outbox state for role-scoped operational views."""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                """
                SELECT event_id, notification_id, correlation_id, request_id,
                       case_id, status, attempts, provider_message_id,
                       delivery_status, delivery_failure_reason, created_at,
                       updated_at, last_failure_at, next_retry_at,
                       terminal_failure, lease_expires_at, delivered_at
                FROM notification_outbox
                ORDER BY created_at DESC, event_id DESC
                LIMIT %s
                """,
                (max(1, min(limit, 250)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_expired_leases(self, event_id: str | None = None) -> int:
        """
        Recover notifications with expired leases back to pending status.
        
        This is called periodically by a background task to handle worker crashes.
        Returns the number of recovered notifications.
        """
        with psycopg.connect(self.database_url) as connection:
            cursor = connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending',
                    locked_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE status = 'processing'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at < NOW()
                AND (%s::text IS NULL OR event_id = %s)
                RETURNING event_id
                """, (event_id, event_id)
            )
            recovered_rows = cursor.fetchall()
            connection.commit()
            return len(recovered_rows)

    def requeue(self, event_id: str, available_at: datetime | None = None, failure_reason: str | None = None) -> None:
        """
        Requeue a notification back to pending status (e.g., after a transient failure).
        Clears the lease so it can be reclaimed by another worker.
        """
        with psycopg.connect(self.database_url) as connection:
            connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending',
                    delivery_failure_reason = %s,
                    last_failure_at = NOW(),
                    next_retry_at = CASE WHEN attempts >= %s THEN NULL ELSE COALESCE(%s, NOW() + make_interval(secs => LEAST(600, POWER(2, attempts) * 10))) END,
                    terminal_failure = attempts >= %s,
                    available_at = CASE WHEN attempts >= %s THEN NOW() ELSE COALESCE(%s, NOW()) END,
                    locked_at = NULL,
                    lease_expires_at = NULL,
                    updated_at = NOW()
                WHERE event_id = %s
                """,
                (failure_reason, self.max_attempts, available_at, self.max_attempts, self.max_attempts, available_at, event_id),
            )
            connection.commit()

    def mark_failed(self, event_id: str, failure_reason: str) -> None:
        """Record a terminal delivery failure after retry exhaustion."""
        self.requeue(event_id, failure_reason=failure_reason)

    def all(self) -> list[dict]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            return connection.execute(
                """
                SELECT event_id, notification_id, status, attempts, delivered_at
                FROM notification_outbox
                ORDER BY created_at, event_id
                """
            ).fetchall()
