import os
import sys
from datetime import datetime, timezone

import psycopg
import pytest

from contracts.audit import AuditRepository
from contracts.event_bus import LocalEventBus
from contracts.events import EventEnvelope, EventType
from contracts.models import Case, Notification

NOTIFY_SVC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "services", "notify-svc"))
if NOTIFY_SVC_DIR not in sys.path:
    sys.path.insert(0, NOTIFY_SVC_DIR)

from postgres_notification_repository import PostgreSQLNotificationRepository
from postgres_notification_outbox import PostgreSQLNotificationOutbox
from notification_service import NotificationService


DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL tests",
)
TEST_PREFIX = "PG-NOTIFY-FLOW-"
CASE_ID = f"{TEST_PREFIX}CASE"
NOTIFICATION_PREFIX = f"NOTIFY-{CASE_ID}-"


@pytest.fixture
def notification_rows():
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM notifications WHERE notification_id LIKE %s",
            (f"{NOTIFICATION_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM notification_outbox WHERE notification_id LIKE %s",
            (f"{NOTIFICATION_PREFIX}%",),
        )
    yield
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM notifications WHERE notification_id LIKE %s",
            (f"{NOTIFICATION_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM notification_outbox WHERE notification_id LIKE %s",
            (f"{NOTIFICATION_PREFIX}%",),
        )


def notification_event():
    case = Case(case_id=CASE_ID, request_id=f"{TEST_PREFIX}REQUEST")
    return EventEnvelope.notifications_requested(
        case,
        [f"{TEST_PREFIX}D-1", f"{TEST_PREFIX}D-2"],
        correlation_id=case.case_id,
    )


def make_service(sent: list):
    assert DATABASE_URL is not None
    return NotificationService(
        PostgreSQLNotificationRepository(DATABASE_URL),
        sent.append,
        audit=AuditRepository(),
    )


def test_notification_is_persisted_and_sent_with_case_context(notification_rows):
    sent = []
    service = make_service(sent)

    outputs = service.handle_request(notification_event())
    notifications = [
        item for item in service.repository.all() if item.case_id == CASE_ID
    ]

    assert len(outputs) == 2
    assert all(event.event_type == EventType.NOTIFICATION_SENT for event in outputs)
    assert len(notifications) == 2
    assert {item.donor_id for item in notifications} == {
        f"{TEST_PREFIX}D-1",
        f"{TEST_PREFIX}D-2",
    }
    assert all(item.status.value == "sent" for item in notifications)
    assert all(TEST_PREFIX in item.message for item in notifications)
    assert len(sent) == 2


def test_duplicate_notification_request_is_suppressed_across_instances(notification_rows):
    first_instance_sent = []
    second_instance_sent = []
    event = notification_event()

    first_instance = make_service(first_instance_sent)
    second_instance = make_service(second_instance_sent)
    first_outputs = first_instance.handle_request(event)
    second_outputs = second_instance.handle_request(event)

    assert len(first_outputs) == 2
    assert second_outputs == []
    assert len(first_instance_sent) == 2
    assert second_instance_sent == []
    assert len(
        [item for item in second_instance.repository.all() if item.case_id == CASE_ID]
    ) == 2


def test_event_bus_suppresses_duplicate_notification_event(notification_rows):
    sent = []
    service = make_service(sent)
    bus = LocalEventBus()
    bus.subscribe(EventType.NOTIFICATIONS_REQUESTED, service.handle_request)
    event = notification_event()

    assert len(bus.publish(event)) == 2
    assert bus.publish(event) == []
    assert len(sent) == 2


def test_outbox_retry_survives_new_repository_instance(notification_rows):
    outbox = PostgreSQLNotificationOutbox(DATABASE_URL)
    item = Notification(
        notification_id=f"{NOTIFICATION_PREFIX}RETRY",
        case_id=CASE_ID,
        request_id=f"{TEST_PREFIX}REQUEST",
        donor_id=f"{TEST_PREFIX}D-RETRY",
        message="retry",
        created_at=datetime.now(timezone.utc),
    )

    assert outbox.enqueue("OUTBOX-RETRY", item) is True
    claimed = outbox.claim_pending()
    assert len(claimed) == 1
    outbox.requeue(claimed[0][0], available_at=datetime.now(timezone.utc))

    restarted = PostgreSQLNotificationOutbox(DATABASE_URL)
    assert [event_id for event_id, _ in restarted.claim_pending()] == ["OUTBOX-RETRY"]


def test_targeted_task_does_not_drain_another_notification(notification_rows):
    from uuid import uuid4
    outbox = PostgreSQLNotificationOutbox(DATABASE_URL)
    ids = [f"TASK-{uuid4().hex}", f"TASK-{uuid4().hex}"]
    for index, event_id in enumerate(ids):
        outbox.enqueue(event_id, Notification(notification_id=f"{NOTIFICATION_PREFIX}TASK-{index}", case_id=CASE_ID,
            request_id=f"{TEST_PREFIX}REQUEST", donor_id=f"{TEST_PREFIX}D-{index}", message="Scoped task", created_at=datetime.now(timezone.utc)))
    sent = []
    service = make_service(sent)
    service.deliver_pending(limit=1, event_id=ids[1])
    assert len(sent) == 1
    assert sent[0].notification_id == f"{NOTIFICATION_PREFIX}TASK-1"
    remaining = outbox.claim_pending(event_id=ids[0])
    assert [event_id for event_id, _ in remaining] == [ids[0]]
