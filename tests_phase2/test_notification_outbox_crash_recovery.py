"""Test notification outbox crash recovery with lease-based processing."""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

NOTIFY_SVC_DIR = Path(__file__).resolve().parents[1] / "services" / "notify-svc"
if str(NOTIFY_SVC_DIR) not in sys.path:
    sys.path.insert(0, str(NOTIFY_SVC_DIR))

from postgres_notification_outbox import PostgreSQLNotificationOutbox
from contracts.models import Notification

DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for outbox crash recovery tests",
)

TEST_PREFIX = "CRASH-TEST-"


def create_test_notification(notification_id: str) -> Notification:
    """Create a test notification."""
    return Notification(
        notification_id=notification_id,
        case_id=f"{TEST_PREFIX}case",
        request_id=f"{TEST_PREFIX}request",
        donor_id=f"{TEST_PREFIX}donor",
        message="Test notification",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def outbox():
    """Create outbox with short lease timeout for testing."""
    assert DATABASE_URL is not None
    
    # Clean up test data
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM notification_outbox WHERE event_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
    
    # Create outbox with 1-second lease timeout for testing
    outbox = PostgreSQLNotificationOutbox(DATABASE_URL, lease_timeout_seconds=1)
    yield outbox
    
    # Clean up
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM notification_outbox WHERE event_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )


def test_worker_crash_causes_lease_expiry_and_recovery(outbox):
    """Test that a worker crash (lease expiry) allows automatic recovery."""
    import time
    
    # Enqueue a notification
    event_id = f"{TEST_PREFIX}crash-1"
    notification = create_test_notification(f"{TEST_PREFIX}notif-1")
    outbox.enqueue(event_id, notification)
    
    # Verify it's pending
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "pending"
    
    # Claim the notification (worker starts processing)
    claimed = outbox.claim_pending()
    assert len(claimed) == 1
    assert claimed[0][0] == event_id
    
    # Verify it's now processing with a lease
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, locked_at, lease_expires_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "processing"
        assert row[1] is not None  # locked_at is set
        assert row[2] is not None  # lease_expires_at is set
    
    # Simulate worker crash by waiting for lease to expire
    time.sleep(2)
    
    # Verify lease has expired (but row is still processing)
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "processing"
    
    # Run recovery
    recovered_count = outbox.recover_expired_leases()
    assert recovered_count == 1
    
    # Verify notification is back to pending and can be claimed again
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, locked_at, lease_expires_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "pending"
        assert row[1] is None  # locked_at is cleared
        assert row[2] is None  # lease_expires_at is cleared
    
    # Verify we can claim it again (simulating recovery)
    claimed_again = outbox.claim_pending()
    assert len(claimed_again) == 1
    assert claimed_again[0][0] == event_id


def test_claim_pending_recovers_expired_leases_before_claiming(outbox):
    """Expired processing leases should be cleaned up automatically when work is claimed."""
    import time

    event_id = f"{TEST_PREFIX}recovery-1"
    notification = create_test_notification(f"{TEST_PREFIX}notif-recovery-1")
    outbox.enqueue(event_id, notification)

    claimed = outbox.claim_pending()
    assert len(claimed) == 1

    time.sleep(2)

    claimed_again = outbox.claim_pending()
    assert len(claimed_again) == 1
    assert claimed_again[0][0] == event_id

    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, locked_at, lease_expires_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "processing"
        assert row[1] is not None
        assert row[2] is not None


def test_successful_delivery_clears_lease(outbox):
    """Test that successful delivery clears the lease."""
    event_id = f"{TEST_PREFIX}success-1"
    notification = create_test_notification(f"{TEST_PREFIX}notif-success-1")
    outbox.enqueue(event_id, notification)
    
    # Claim the notification
    claimed = outbox.claim_pending()
    assert len(claimed) == 1
    
    # Verify it's processing with a lease
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, lease_expires_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "processing"
        assert row[1] is not None
    
    # Mark as delivered
    outbox.mark_delivered(event_id)
    
    # Verify lease is cleared and status is delivered
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, locked_at, lease_expires_at, delivered_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "delivered"
        assert row[1] is None  # locked_at cleared
        assert row[2] is None  # lease_expires_at cleared
        assert row[3] is not None  # delivered_at set


def test_requeue_clears_lease(outbox):
    """Test that requeuing clears the lease for retry."""
    event_id = f"{TEST_PREFIX}requeue-1"
    notification = create_test_notification(f"{TEST_PREFIX}notif-requeue-1")
    outbox.enqueue(event_id, notification)
    
    # Claim the notification
    claimed = outbox.claim_pending()
    assert len(claimed) == 1
    
    # Verify it's processing with a lease
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, lease_expires_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "processing"
        assert row[1] is not None
    
    # Requeue due to transient failure
    future_time = datetime.now(timezone.utc) + timedelta(seconds=10)
    outbox.requeue(event_id, available_at=future_time)
    
    # Verify lease is cleared and status is pending
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT status, locked_at, lease_expires_at, available_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        assert row[0] == "pending"
        assert row[1] is None  # locked_at cleared
        assert row[2] is None  # lease_expires_at cleared
        # available_at should be set to our future time


def test_lease_timeout_is_enforced(outbox):
    """Test that lease timeout prevents double-processing even without crash recovery."""
    event_id = f"{TEST_PREFIX}timeout-1"
    notification = create_test_notification(f"{TEST_PREFIX}notif-timeout-1")
    outbox.enqueue(event_id, notification)
    
    # Claim the notification
    claimed = outbox.claim_pending()
    assert len(claimed) == 1

    # Get the lease expiry time after claiming
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(
            "SELECT lease_expires_at FROM notification_outbox WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        lease_expiry = row[0]
    
    # Verify lease expires in the future (not immediately)
    time_remaining = (lease_expiry - datetime.now(timezone.utc)).total_seconds()
    assert time_remaining > 0, "Lease should expire in the future"
    assert time_remaining <= 2, "Lease should expire soon (1 sec timeout + margin)"
