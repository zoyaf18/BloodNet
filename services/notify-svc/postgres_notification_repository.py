"""PostgreSQL-backed notification repository."""

from __future__ import annotations

import psycopg

from psycopg.types.json import Jsonb

from contracts.models import Notification


class PostgreSQLNotificationRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def get(self, notification_id: str) -> Notification | None:
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT payload
                    FROM notifications
                    WHERE notification_id = %s
                    """,
                    (notification_id,),
                )
                row = cursor.fetchone()

            if row is None:
                return None

            return Notification.model_validate(row[0])

    def save(self, notification: Notification) -> None:
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO notifications (
                        notification_id,
                        payload,
                        updated_at
                    )
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (notification_id)
                    DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    (
                        notification.notification_id,
                        Jsonb(notification.model_dump(mode="json")),
                    ),
                )

    def all(self) -> list[Notification]:
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT payload
                    FROM notifications
                    ORDER BY created_at, notification_id
                    """
                )
                rows = cursor.fetchall()

            return [
                Notification.model_validate(row[0])
                for row in rows
            ]
