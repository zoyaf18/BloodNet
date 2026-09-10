from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NOTIFY_SVC = ROOT / "services" / "notify-svc"
if str(NOTIFY_SVC) not in sys.path:
    sys.path.insert(0, str(NOTIFY_SVC))

from contracts.event_bus import LocalEventBus
from contracts.events import EventEnvelope, EventType
from contracts.models import Case, NotificationStatus, ReservationState
from notification_service import NotificationRepository, NotificationService


def notification_event():
    case = Case(case_id="CASE-N", request_id="REQ-N")
    return EventEnvelope.notifications_requested(
        case,
        ["D-1", "D-2"],
        correlation_id=case.case_id,
    )


def test_notification_is_persisted_and_sent_with_case_context():
    repository = NotificationRepository()
    sent = []
    service = NotificationService(repository, sent.append)

    outputs = service.handle_request(notification_event())

    assert len(outputs) == 2
    assert all(event.event_type == EventType.NOTIFICATION_SENT for event in outputs)
    notifications = repository.all()
    assert len(notifications) == 2
    assert {item.donor_id for item in notifications} == {"D-1", "D-2"}
    assert all(item.status.value == "sent" for item in notifications)
    assert all("REQ-N" in item.message for item in notifications)
    assert len(sent) == 2


def test_duplicate_notification_request_does_not_send_twice():
    repository = NotificationRepository()
    sent = []
    service = NotificationService(repository, sent.append)
    event = notification_event()

    service.handle_request(event)
    service.handle_request(
        EventEnvelope.notifications_requested(
            event.payload.case,
            event.payload.donor_ids,
            correlation_id=event.correlation_id,
        )
    )

    assert len(repository.all()) == 2
    assert len(sent) == 2


@pytest.mark.parametrize(
    ("receipt_status", "expected_status"),
    [("delivered", NotificationStatus.SENT), ("failed", NotificationStatus.FAILED)],
)
def test_provider_receipt_projects_terminal_delivery_state(receipt_status, expected_status):
    repository = NotificationRepository()
    service = NotificationService(repository)
    service.handle_request(notification_event())

    notification = service.record_delivery_receipt(
        provider_message_id="NOTIFY-CASE-N-D-1",
        delivery_status=receipt_status,
        failure_reason="provider rejected message" if receipt_status == "failed" else None,
    )

    assert notification.status is expected_status
    assert notification.delivery_status == receipt_status
    if receipt_status == "delivered":
        assert notification.delivered_at is not None
    else:
        assert notification.delivery_failure_reason == "provider rejected message"


def test_in_app_blood_drive_notification_is_idempotent_and_hospital_scoped():
    repository = NotificationRepository()
    service = NotificationService(repository)

    first = service.create_in_app_notification(
        notification_id="DRIVE-NOTIFY-1-HOSPITAL-1",
        case_id="DRIVE-1",
        request_id="FORECAST-1",
        recipient_type="hospital",
        recipient_id="HOSPITAL-1",
        message="A regional donation drive is ready for review in Pune.",
    )
    second = service.create_in_app_notification(
        notification_id="DRIVE-NOTIFY-1-HOSPITAL-1",
        case_id="DRIVE-1",
        request_id="FORECAST-1",
        recipient_type="hospital",
        recipient_id="HOSPITAL-1",
        message="A duplicate drive alert must not be created.",
    )

    assert second.notification_id == first.notification_id
    assert second.recipient_type == "hospital"
    assert second.recipient_id == "HOSPITAL-1"
    assert len(repository.all()) == 1


def test_event_bus_suppresses_duplicate_notification_event():
    repository = NotificationRepository()
    sent = []
    service = NotificationService(repository, sent.append)
    bus = LocalEventBus()
    bus.subscribe(EventType.NOTIFICATIONS_REQUESTED, service.handle_request)
    event = notification_event()

    assert len(bus.publish(event)) == 2
    assert bus.publish(event) == []
    assert len(sent) == 2


@pytest.mark.parametrize("state", [ReservationState.AWAITING_APPROVAL, ReservationState.REJECTED])
def test_notification_requires_approved_reservation_state(state):
    repository = NotificationRepository()
    service = NotificationService(repository, lambda notification: None)
    case = Case(
        case_id="CASE-GATE",
        request_id="REQ-GATE",
        reservation_state=state,
    )

    with pytest.raises(ValueError, match="approved reservation state"):
        service.handle_request(
            EventEnvelope.notifications_requested(case, ["D-1"], correlation_id=case.case_id)
        )

    assert repository.all() == []


def test_enabled_production_notifications_fail_fast_when_dispatch_is_incomplete(monkeypatch):
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setenv("BLOODNET_ENABLE_NOTIFICATIONS", "true")
    monkeypatch.setenv("BLOODNET_NOTIFICATION_PROVIDER_URL", "https://provider.invalid/send")
    monkeypatch.setenv("BLOODNET_NOTIFICATION_PROVIDER_TOKEN", "configured")
    for name in (
        "GOOGLE_CLOUD_PROJECT",
        "GCP_PROJECT_ID",
        "BLOODNET_CLOUD_TASKS_QUEUE",
        "BLOODNET_NOTIFICATION_DELIVERY_URL",
        "BLOODNET_RUNTIME_SERVICE_ACCOUNT",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="dispatch is incomplete"):
        NotificationService(NotificationRepository())
