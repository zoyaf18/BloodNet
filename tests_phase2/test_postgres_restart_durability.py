from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys

import psycopg
import pytest
from fastapi.testclient import TestClient

from contracts.auth import Identity, get_identity
from contracts.events import EventEnvelope
from contracts.models import Case

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL tests",
)
CASE_ID = "PG-RESTART-DURABILITY-CASE"
DONOR_ID = "PG-RESTART-DURABILITY-DONOR"
NOTIFICATION_ID = f"NOTIFY-{CASE_ID}-{DONOR_ID}"
AUDIT_ID = f"AUDIT-{NOTIFICATION_ID}"


def load_api(alias: str):
    spec = importlib.util.spec_from_file_location(alias, ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[alias] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def durable_rows():
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM notifications WHERE notification_id = %s",
            (NOTIFICATION_ID,),
        )
        connection.execute(
            "DELETE FROM audit_records WHERE audit_id = %s",
            (AUDIT_ID,),
        )
        connection.execute(
            "DELETE FROM notification_outbox WHERE notification_id = %s",
            (NOTIFICATION_ID,),
        )
    yield
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM notifications WHERE notification_id = %s",
            (NOTIFICATION_ID,),
        )
        connection.execute(
            "DELETE FROM audit_records WHERE audit_id = %s",
            (AUDIT_ID,),
        )
        connection.execute(
            "DELETE FROM notification_outbox WHERE notification_id = %s",
            (NOTIFICATION_ID,),
        )


def test_notification_and_audit_survive_application_restart(durable_rows):
    assert DATABASE_URL is not None
    first = load_api("bloodnet_restart_first")
    event = EventEnvelope.notifications_requested(
        Case(case_id=CASE_ID, request_id="PG-RESTART-DURABILITY-REQUEST"),
        [DONOR_ID],
        correlation_id=CASE_ID,
    )

    outputs = first.match_svc.workflow_store.notifications.handle_request(event)

    assert len(outputs) == 1
    assert outputs[0].payload.notification.notification_id == NOTIFICATION_ID

    second = load_api("bloodnet_restart_second")
    second.match_svc.app.dependency_overrides[get_identity] = lambda: Identity(
        "PG-RESTART-AUDITOR", "auditor"
    )
    client = TestClient(second.app)

    notifications_response = client.get(
        f"/match-svc/api/v1/notifications?case_id={CASE_ID}"
    )
    audit_response = client.get(f"/match-svc/api/v1/audit?case_id={CASE_ID}")

    assert notifications_response.status_code == 200
    assert audit_response.status_code == 200
    notifications = notifications_response.json()["notifications"]
    audit_events = audit_response.json()["events"]
    assert [item["notification_id"] for item in notifications] == [
        "NOTIFICATION-REDACTED"
    ]
    assert [item["audit_id"] for item in audit_events] == ["AUDIT-REDACTED"]
    assert DONOR_ID not in notifications_response.text
    assert DONOR_ID not in audit_response.text
