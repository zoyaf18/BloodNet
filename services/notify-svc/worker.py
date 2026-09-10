"""Cloud Run Job entrypoint for durable notification delivery and lease recovery."""

from __future__ import annotations

import os

from notification_service import NotificationService
from postgres_notification_repository import PostgreSQLNotificationRepository


def main() -> None:
    database_url = os.environ["BLOODNET_DATABASE_URL"]
    service = NotificationService(PostgreSQLNotificationRepository(database_url))
    service.outbox.recover_expired_leases()
    service.deliver_pending(limit=int(os.getenv("BLOODNET_NOTIFICATION_BATCH_SIZE", "100")))


if __name__ == "__main__":
    main()