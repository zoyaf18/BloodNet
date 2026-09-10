from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts.audit import AuditRepository
from contracts.event_bus import LocalEventBus
from contracts.events import EventEnvelope, EventType
from contracts.models import AuditRecord, BloodGroup, Component, Request, Urgency
from contracts.security_policy import validate_minimal_data_handling


def _request() -> Request:
    return Request(
        request_id="REQ-HARDEN-001",
        group=BloodGroup.O_NEG,
        component=Component.RBC,
        qty=2,
        hospital_id="H-1",
        urgency=Urgency.CRITICAL,
        required_by=datetime.now(timezone.utc) + timedelta(hours=2),
        source_channel="test",
    )


def test_failed_handler_is_quarantined_and_replayable():
    bus = LocalEventBus()

    def failing_handler(event):
        raise RuntimeError("processing failure")

    bus.subscribe(EventType.REQUEST_CREATED, failing_handler)
    event = EventEnvelope.request_created(_request())

    outputs = bus.publish(event)

    assert outputs == []
    assert len(bus.dead_letter) == 1
    assert bus.dead_letter[0].event_id == event.event_id
    replayed = bus.replay_dead_letters(limit=10)
    assert len(replayed) == 1
    assert replayed[0].event_id == event.event_id


def test_audit_repository_does_not_overwrite_existing_record():
    repo = AuditRepository()
    base = {
        "audit_id": "AUD-IMMUTABLE-1",
        "action": "approval_approved",
        "request_id": "REQ-HARDEN-001",
        "case_id": "CASE-HARDEN-001",
        "at": datetime.now(timezone.utc),
    }
    record = AuditRecord(**base)
    repo.append(record)
    repo.append(record.model_copy(update={"action": "approval_rejected"}))

    assert repo.all() == [record]


def test_durable_replay_queue_persists_from_previous_instance():
    from contracts.event_bus import DurableEventQueue

    queue_path = Path("test_hardening_queue.sqlite")
    if queue_path.exists():
        queue_path.unlink()

    queue = DurableEventQueue(path=str(queue_path))
    queue.enqueue(EventEnvelope.request_created(_request()))
    queue.close()

    reopened = DurableEventQueue(path=str(queue_path))
    assert len(reopened.pending()) == 1
    assert reopened.pending()[0].event_type == EventType.REQUEST_CREATED
    reopened.close()


def test_consumer_offsets_are_persisted_per_subscription():
    from contracts.event_bus import ConsumerOffsetStore

    offsets = ConsumerOffsetStore(path="test_hardening_offsets.sqlite")
    offsets.record("audit-worker", "AUDIT", 42)
    offsets.record("audit-worker", "AUDIT", 43)
    assert offsets.get("audit-worker", "AUDIT") == 43
    assert offsets.get("other", "AUDIT") is None
    offsets.close()


def test_audit_records_chain_hashes_across_instances():
    first = AuditRecord(
        audit_id="AUD-CHAIN-1",
        action="request_created",
        request_id="REQ-CHAIN-1",
        case_id="CASE-CHAIN-1",
        at=datetime.now(timezone.utc),
    )
    second = AuditRecord(
        audit_id="AUD-CHAIN-2",
        action="approval_approved",
        request_id="REQ-CHAIN-1",
        case_id="CASE-CHAIN-1",
        at=datetime.now(timezone.utc),
        prev_audit_hash=first.record_hash,
    )

    assert second.prev_audit_hash == first.record_hash
    assert second.record_hash.startswith("sha256:")
    assert first.record_hash != second.record_hash


def test_stricter_phi_policy_redacts_and_rejects_excessive_data():
    payload = {
        "case_id": "CASE-123",
        "donor": {
            "name": "Alice Example",
            "phone": "+91 9876543210",
            "email": "alice@example.com",
            "consent": True,
        },
    }

    ok, issues, safe = validate_minimal_data_handling(payload)
    assert ok is False
    assert issues
    assert "Alice Example" not in str(safe)
    assert "+91 9876543210" not in str(safe)
    assert "alice@example.com" not in str(safe)
    assert "CASE-123" in str(safe)
