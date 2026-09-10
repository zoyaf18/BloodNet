"""Local persisted notification adapter for the BloodNet demo."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import inspect
import os
import sqlite3
import json
import logging
from dataclasses import dataclass
from typing import Callable, Iterator
from urllib.request import Request, urlopen

from contracts.events import (
    EventEnvelope,
    EventType,
    NotificationsRequestedPayload,
)
from contracts.audit import AuditRepository
from contracts.models import AuditRecord, Notification, NotificationStatus, ReservationState
from postgres_notification_repository import PostgreSQLNotificationRepository
from postgres_notification_outbox import PostgreSQLNotificationOutbox

logger = logging.getLogger(__name__)


class NotificationRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self._memory_connection = sqlite3.connect(":memory:") if db_path is None else None
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS notifications (notification_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            connection.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_connection is not None:
            yield self._memory_connection
            return
        with sqlite3.connect(self._db_path) as connection:
            yield connection

    def get(self, notification_id: str) -> Notification | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM notifications WHERE notification_id = ?", (notification_id,)).fetchone()
        return Notification.model_validate_json(row[0]) if row else None

    def save(self, notification: Notification) -> None:
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO notifications(notification_id, payload) VALUES (?, ?)", (notification.notification_id, notification.model_dump_json()))
            connection.commit()

    def all(self) -> list[Notification]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM notifications ORDER BY rowid").fetchall()
        return [Notification.model_validate_json(row[0]) for row in rows]


class DemoNotificationSender:
    """Deterministic sender seam for a console/sandbox provider."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification, provider_idempotency_key: str | None = None) -> None:
        if any(item.notification_id == notification.notification_id for item in self.sent):
            return
        self.sent.append(notification)


@dataclass(frozen=True)
class ProviderSendResult:
    provider_message_id: str
    delivery_status: str = "accepted"


class ConfiguredNotificationProvider:
    """HTTP adapter for a managed SMS/WhatsApp/FCM provider gateway."""

    def __init__(self, endpoint: str, token: str, timeout_seconds: float = 10.0) -> None:
        self.endpoint = endpoint
        self.token = token
        self.timeout_seconds = timeout_seconds

    def send(self, notification: Notification, provider_idempotency_key: str | None = None) -> ProviderSendResult:
        body = json.dumps({
            "notification_id": notification.notification_id,
            "idempotency_key": provider_idempotency_key or notification.notification_id,
            "channel": notification.channel,
            "recipient_id": notification.donor_id,
            "message": notification.message,
        }).encode("utf-8")
        request = Request(self.endpoint, data=body, method="POST", headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Idempotency-Key": provider_idempotency_key or notification.notification_id,
        })
        with urlopen(request, timeout=self.timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        provider_message_id = result.get("provider_message_id") or result.get("message_id")
        if not provider_message_id:
            raise RuntimeError("Notification provider returned no provider_message_id.")
        return ProviderSendResult(str(provider_message_id), str(result.get("status") or "accepted"))


class SMTPNotificationProvider:
    """Send approved donor outreach using the existing SMTP configuration."""

    def send(self, notification: Notification, provider_idempotency_key: str | None = None) -> ProviderSendResult:
        import hashlib
        from uuid import UUID
        from contracts.auth_repository import AuthRepository
        from contracts.email_service import send_email
        if notification.channel != "email":
            raise RuntimeError("SMTP notifications require the email channel")
        repository = AuthRepository(os.environ["BLOODNET_DATABASE_URL"])
        user_id = UUID(notification.donor_id)
        user = repository.get_user_by_id(user_id)
        profile = repository.get_donor_profile(user_id)
        if not user or not user.email_verified or user.status != "active" or not profile or not profile.get("consent_contact") or "email" not in profile.get("notification_channels", []):
            raise RuntimeError("Recipient is not eligible for email outreach")
        from contracts.outreach_policy import current_recipient_allowed
        if not current_recipient_allowed(os.environ["BLOODNET_DATABASE_URL"], notification.donor_id, notification.request_id):
            raise RuntimeError("Recipient is outside the current request outreach scope")
        key = hashlib.sha256((provider_idempotency_key or notification.notification_id).encode()).hexdigest()
        message_id = send_email(recipient=user.email, subject="BloodNet donation request",
                                text=notification.message, message_id=f"<{key}@notifications.bloodnet>")
        if not message_id:
            raise RuntimeError("SMTP delivery is not configured")
        return ProviderSendResult(message_id, "accepted")


class CloudTasksNotificationDispatcher:
    """Enqueue durable outbox delivery without coupling it to request latency."""

    def __init__(self, project: str, location: str, queue: str, target_url: str, service_account: str) -> None:
        from google.cloud import tasks_v2
        self.client = tasks_v2.CloudTasksClient()
        self.queue_path = self.client.queue_path(project, location, queue)
        self.target_url = target_url
        self.service_account = service_account

    def enqueue(self, event_id: str) -> None:
        from google.cloud import tasks_v2
        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self.target_url,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"event_id": event_id}).encode("utf-8"),
                "oidc_token": {
                    "service_account_email": self.service_account,
                    # Cloud Run authenticates against the service origin, not
                    # the worker route appended to it.
                    "audience": os.getenv("BLOODNET_SERVICE_AUDIENCE") or
                                self.target_url.split("/match-svc/", 1)[0],
                },
            }
        }
        self.client.create_task(parent=self.queue_path, task=task)


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository | None = None,
        sender: Callable[[Notification], None] | None = None,
        audit: AuditRepository | None = None,
    ) -> None:
        if repository is None:
            self.repository = PostgreSQLNotificationRepository(
                os.environ["BLOODNET_DATABASE_URL"]
            )
        else:
            self.repository = repository
        environment = os.getenv("BLOODNET_ENV", "development").lower()
        production_like = environment in {"production", "prod", "demo"}
        notifications_enabled = os.getenv("BLOODNET_ENABLE_NOTIFICATIONS", "false").lower() == "true"
        self.delivery_enabled = sender is not None or notifications_enabled or not production_like
        if sender is not None:
            self.sender = sender
        elif notifications_enabled:
            endpoint = os.getenv("BLOODNET_NOTIFICATION_PROVIDER_URL")
            token = os.getenv("BLOODNET_NOTIFICATION_PROVIDER_TOKEN")
            smtp_selected = os.getenv("BLOODNET_NOTIFICATION_PROVIDER", "").lower() == "smtp"
            if smtp_selected:
                if not all(os.getenv(key) for key in ("BLOODNET_SMTP_HOST", "BLOODNET_SMTP_USERNAME", "BLOODNET_SMTP_PASSWORD", "BLOODNET_EMAIL_FROM")):
                    raise RuntimeError("SMTP notification delivery configuration is incomplete")
                self.sender = SMTPNotificationProvider().send
            elif production_like and (not endpoint or not token):
                raise RuntimeError("Production notification delivery requires BLOODNET_NOTIFICATION_PROVIDER_URL and BLOODNET_NOTIFICATION_PROVIDER_TOKEN.")
            else:
                self.sender = (
                ConfiguredNotificationProvider(endpoint, token).send
                if endpoint and token
                else DemoNotificationSender().send
                )
        elif production_like:
            def disabled_sender(notification):
                raise RuntimeError("External notification delivery is disabled")
            self.sender = disabled_sender
        else:
            self.sender = DemoNotificationSender().send
        self.audit = audit or AuditRepository()
        self.outbox = (
            PostgreSQLNotificationOutbox(self.repository.database_url)
            if isinstance(self.repository, PostgreSQLNotificationRepository)
            else None
        )
        self.dispatcher = None
        task_settings = {
            "project": os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
            "queue": os.getenv("BLOODNET_CLOUD_TASKS_QUEUE"),
            "target_url": os.getenv("BLOODNET_NOTIFICATION_DELIVERY_URL"),
            "service_account": os.getenv("BLOODNET_RUNTIME_SERVICE_ACCOUNT"),
        }
        configured_task_settings = [name for name, value in task_settings.items() if value]
        if production_like and notifications_enabled and len(configured_task_settings) != len(task_settings):
            missing = [name for name, value in task_settings.items() if not value]
            raise RuntimeError("Production notification dispatch is incomplete: " + ", ".join(missing))
        if self.outbox is not None and len(configured_task_settings) == len(task_settings):
            self.dispatcher = CloudTasksNotificationDispatcher(
                str(task_settings["project"]),
                os.environ.get("GOOGLE_CLOUD_LOCATION", "asia-south1"),
                str(task_settings["queue"]),
                str(task_settings["target_url"]),
                str(task_settings["service_account"]),
            )

    def _send(self, notification: Notification, provider_idempotency_key: str) -> ProviderSendResult:
        """Pass the provider key while retaining compatibility with old senders."""
        if len(inspect.signature(self.sender).parameters) >= 2:
            result = self.sender(notification, provider_idempotency_key)
        else:
            result = self.sender(notification)
        if isinstance(result, ProviderSendResult):
            return result
        return ProviderSendResult(provider_message_id=notification.notification_id)

    def handle_request(
        self, event: EventEnvelope[NotificationsRequestedPayload]
    ) -> list[EventEnvelope]:
        if event.event_type != EventType.NOTIFICATIONS_REQUESTED:
            if event.event_type != EventType.NOTIFICATION_REQUESTED:
                raise ValueError(f"Unsupported event type: {event.event_type}")

        case = event.payload.case
        if case.reservation_state in {
            ReservationState.AWAITING_APPROVAL,
            ReservationState.REJECTED,
        }:
            raise ValueError("Notifications require an approved reservation state.")

        sent_events: list[EventEnvelope] = []
        queued_notifications: list[str] = []
        for donor_id in event.payload.donor_ids:
            notification_id = f"NOTIFY-{case.case_id}-{donor_id}"
            existing = self.repository.get(notification_id)
            if existing is not None:
                continue

            notification = Notification(
                notification_id=notification_id,
                case_id=case.case_id,
                request_id=case.request_id,
                donor_id=donor_id,
                channel=os.getenv("BLOODNET_NOTIFICATION_DEFAULT_CHANNEL", "sms"),
                message=(
                    f"BloodNet request {case.request_id}: please respond for "
                    "an urgent donation opportunity."
                ),
                created_at=datetime.now(timezone.utc),
            )
            event_id = f"{event.event_id}:{donor_id}"
            if self.outbox is not None:
                self.outbox.enqueue(
                    event_id,
                    notification,
                    correlation_id=event.correlation_id,
                    provider_idempotency_key=notification.notification_id,
                )
                if self.dispatcher is not None:
                    self.dispatcher.enqueue(event_id)
                queued_notifications.append(notification_id)
            else:
                provider_result = self._send(notification, notification.notification_id)
                notification.status = NotificationStatus.SENT
                notification.sent_at = datetime.now(timezone.utc)
                notification.provider_message_id = provider_result.provider_message_id
                notification.delivery_status = provider_result.delivery_status
                self.repository.save(notification)
                self._audit(notification)

            if self.outbox is None:
                sent_events.append(
                    EventEnvelope.notification_sent(
                        notification,
                        correlation_id=event.correlation_id,
                    )
                )

        if self.outbox is not None:
            # Cloud Tasks owns provider delivery when configured. The in-process
            # drain remains a local/test fallback only; doing both would create
            # an avoidable second worker race around the same lease.
            if self.dispatcher is None and self.delivery_enabled:
                self.deliver_pending()
            for notification_id in queued_notifications:
                notification = self.repository.get(notification_id)
                if notification is not None and notification.status is NotificationStatus.SENT:
                    sent_events.append(
                        EventEnvelope.notification_sent(
                            notification,
                            correlation_id=event.correlation_id,
                        )
                    )
        return sent_events

    def create_in_app_notification(
        self,
        *,
        notification_id: str,
        case_id: str,
        request_id: str,
        recipient_type: str,
        recipient_id: str,
        message: str,
    ) -> Notification:
        """Persist an in-app notification without sending an external message."""
        existing = self.repository.get(notification_id)
        if existing is not None:
            return existing
        now = datetime.now(timezone.utc)
        notification = Notification(
            notification_id=notification_id,
            case_id=case_id,
            request_id=request_id,
            donor_id="system",
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            channel="in_app",
            message=message,
            status=NotificationStatus.SENT,
            created_at=now,
            sent_at=now,
            provider_message_id=notification_id,
            delivery_status="delivered",
            delivered_at=now,
        )
        self.repository.save(notification)
        self._audit(notification)
        return notification

    def deliver_pending(self, limit: int = 100, event_id: str | None = None) -> list[EventEnvelope]:
        if self.outbox is None or not self.delivery_enabled:
            return []

        sent_events: list[EventEnvelope] = []
        for event_id, notification in self.outbox.claim_pending(limit, event_id=event_id):
            try:
                provider_result = self._send(notification, notification.notification_id)
                notification.status = NotificationStatus.SENT
                notification.sent_at = datetime.now(timezone.utc)
                notification.provider_message_id = provider_result.provider_message_id
                notification.delivery_status = provider_result.delivery_status
                notification.delivered_at = datetime.now(timezone.utc) if provider_result.delivery_status == "delivered" else None
                self.repository.save(notification)
                self._audit(notification)
                self.outbox.mark_delivered(
                    event_id,
                    provider_message_id=provider_result.provider_message_id,
                    delivery_status=provider_result.delivery_status,
                )
                logger.info("notification_delivery_succeeded", extra={"event_id": event_id, "notification_id": notification.notification_id, "delivery_status": notification.delivery_status})
                sent_events.append(
                    EventEnvelope.notification_sent(notification, correlation_id=notification.case_id)
                )
            except Exception as error:
                self.outbox.requeue(event_id, failure_reason=str(error)[:500])
                logger.exception("notification_delivery_failed", extra={"event_id": event_id, "notification_id": notification.notification_id})
                raise
        return sent_events

    def _audit(self, notification: Notification) -> None:
        self.audit.append(
            AuditRecord(
                audit_id=f"AUDIT-{notification.notification_id}",
                action="notification_sent",
                request_id=notification.request_id,
                case_id=notification.case_id,
                details={"donor_id": notification.donor_id, "channel": notification.channel},
                at=notification.sent_at or datetime.now(timezone.utc),
            )
        )

    def record_delivery_receipt(
        self,
        *,
        provider_message_id: str,
        delivery_status: str,
        failure_reason: str | None = None,
    ) -> Notification:
        """Apply an authenticated provider receipt idempotently."""
        notification = next(
            (item for item in self.repository.all() if item.provider_message_id == provider_message_id),
            None,
        )
        if notification is None:
            raise ValueError("Unknown provider message ID.")
        normalized_status = delivery_status.strip().lower()
        successful_statuses = {"delivered", "success", "completed"}
        failed_statuses = {"failed", "failure", "undelivered", "rejected", "expired"}
        notification.delivery_status = normalized_status
        notification.delivery_failure_reason = failure_reason
        if normalized_status in successful_statuses:
            notification.delivered_at = datetime.now(timezone.utc)
        elif normalized_status in failed_statuses:
            notification.status = NotificationStatus.FAILED
        self.repository.save(notification)
        if self.outbox is not None:
            self.outbox.record_receipt(
                provider_message_id,
                delivery_status,
                failure_reason,
            )
        self.audit.append(AuditRecord(
            audit_id=f"AUDIT-RECEIPT-{provider_message_id}-{delivery_status}",
            action="notification_delivery_receipt",
            request_id=notification.request_id,
            case_id=notification.case_id,
            details={"provider_message_id": provider_message_id, "delivery_status": delivery_status, "failure_reason": failure_reason},
            at=datetime.now(timezone.utc),
        ))
        return notification
