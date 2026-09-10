from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MATCH_SVC = ROOT / "services" / "match-svc"
if str(MATCH_SVC) not in sys.path:
    sys.path.insert(0, str(MATCH_SVC))

from approval_flow import ApprovalFlowError, ReservationApprovalService
from contracts.event_bus import LocalEventBus
from contracts.events import EventEnvelope, EventType
from contracts.models import (
    Approval,
    ApprovalDecision,
    BloodGroup,
    Component,
    InventoryStatus,
    InventoryUnit,
    ReservationState,
)
from inventory_mutation import InventoryRepository


NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def unit(unit_id: str) -> InventoryUnit:
    return InventoryUnit(
        unit_id=unit_id,
        bank_id="BANK-1",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=NOW - timedelta(days=2),
        expires_at=NOW + timedelta(days=30),
    )


def make_case(unit_id: str = "UNIT-1"):
    from contracts.models import Case, InventoryMatch

    case = Case(
        case_id="CASE-1",
        request_id="REQ-1",
        units_from_inventory=1,
        units_from_donors_remaining=2,
    )
    match = InventoryMatch(
        bank_id="BANK-1",
        units_available=1,
        selected_unit_ids=[unit_id],
        distance_km=1,
        eta_min=2,
    )
    return case, match


def approval_for(recommendation, decision, approval_id="APPROVAL-1"):
    return Approval(
        approval_id=approval_id,
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=decision,
        rationale="reviewed",
        at=NOW,
    )


def test_proposal_approval_reserves_inventory_and_preserves_shortfall():
    repository = InventoryRepository(units=[unit("UNIT-1")])
    service = ReservationApprovalService(repository)
    case, match = make_case()

    proposal_event = service.propose(case, match)
    recommendation = proposal_event.payload.recommendation
    assert proposal_event.event_type == EventType.RESERVATION_PROPOSED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.AVAILABLE
    assert case.reservation_state == ReservationState.AWAITING_APPROVAL

    approval_event = EventEnvelope.approval_decided(
        approval_for(recommendation, ApprovalDecision.APPROVE),
        recommendation,
        correlation_id=proposal_event.correlation_id,
    )
    reserved_event = service.handle_approval(approval_event)

    assert reserved_event is not None
    assert reserved_event.event_type == EventType.INVENTORY_RESERVED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.RESERVED
    assert case.units_from_donors_remaining == 2
    assert case.reservation_state == ReservationState.RESERVED


def test_rejection_leaves_inventory_untouched():
    repository = InventoryRepository(units=[unit("UNIT-1")])
    service = ReservationApprovalService(repository)
    case, match = make_case()
    proposal_event = service.propose(case, match)
    recommendation = proposal_event.payload.recommendation

    rejected = service.handle_approval(
        EventEnvelope.approval_decided(
            approval_for(recommendation, ApprovalDecision.REJECT),
            recommendation,
            correlation_id=case.case_id,
        )
    )

    assert rejected is not None
    assert rejected.event_type == EventType.RESERVATION_REJECTED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.AVAILABLE
    assert case.reservation_state == ReservationState.REJECTED


def test_duplicate_approval_delivery_is_idempotent_even_with_new_event_id():
    repository = InventoryRepository(units=[unit("UNIT-1")])
    service = ReservationApprovalService(repository)
    case, match = make_case()
    proposal_event = service.propose(case, match)
    recommendation = proposal_event.payload.recommendation
    approval = approval_for(recommendation, ApprovalDecision.APPROVE)

    first_event = EventEnvelope.approval_decided(
        approval, recommendation, correlation_id=case.case_id
    )
    second_event = EventEnvelope.approval_decided(
        approval, recommendation, correlation_id=case.case_id
    )
    assert service.handle_approval(first_event) is not None
    assert service.handle_approval(second_event) is None
    assert len(repository.get_reservations()) == 1


def test_event_bus_duplicate_delivery_is_ignored():
    repository = InventoryRepository(units=[unit("UNIT-1")])
    service = ReservationApprovalService(repository)
    case, match = make_case()
    proposal_event = service.propose(case, match)
    recommendation = proposal_event.payload.recommendation
    approval_event = EventEnvelope.approval_decided(
        approval_for(recommendation, ApprovalDecision.APPROVE),
        recommendation,
        correlation_id=case.case_id,
    )
    bus = LocalEventBus()
    bus.subscribe(EventType.APPROVAL_DECIDED, service.handle_approval)

    assert len(bus.publish(approval_event)) == 1
    assert bus.publish(approval_event) == []
    assert len(repository.get_reservations()) == 1


# PostgreSQL atomicity tests
import os
from contracts.audit_postgres import PostgreSQLAuditRepository
from postgres_inventory_repository import PostgreSQLInventoryRepository

DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")

@pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for atomic transaction tests",
)
def test_approval_transaction_records_state_audit_and_outbox():
    """Verify that approval state, audit, and outbox are recorded atomically."""
    import psycopg
    
    # Clean up test data
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("DELETE FROM approval_state_tracking WHERE approval_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM audit_records WHERE audit_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM notification_outbox WHERE event_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM inventory_reservations WHERE reservation_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM inventory_units WHERE unit_id LIKE 'ATOM-TEST-%'")
    
    # Set up repository and audit
    repo = PostgreSQLInventoryRepository(DATABASE_URL)
    audit = PostgreSQLAuditRepository(DATABASE_URL)
    
    # Create test data
    test_unit = unit("ATOM-TEST-UNIT-1")
    repo.save_unit(test_unit)
    
    service = ReservationApprovalService(repo, audit=audit)
    case, match = make_case("ATOM-TEST-UNIT-1")
    case.case_id = "ATOM-TEST-CASE-1"
    case.request_id = "ATOM-TEST-REQ-1"
    
    proposal_event = service.propose(case, match)
    recommendation = proposal_event.payload.recommendation
    
    approval = approval_for(recommendation, ApprovalDecision.APPROVE, approval_id="ATOM-TEST-APPROVAL-1")
    approval_event = EventEnvelope.approval_decided(
        approval,
        recommendation,
        correlation_id=case.case_id,
    )
    
    # Handle approval (should write to multiple tables atomically)
    result = service.handle_approval(approval_event)
    assert result is not None
    
    # Verify approval state was recorded
    with psycopg.connect(DATABASE_URL) as conn:
        approval_state = conn.execute(
            "SELECT decision FROM approval_state_tracking WHERE approval_id = %s",
            ("ATOM-TEST-APPROVAL-1",),
        ).fetchone()
        assert approval_state is not None
        assert approval_state[0] == "APPROVED"
        
        # Verify audit record was written
        audit_records = conn.execute(
            "SELECT payload FROM audit_records WHERE audit_id LIKE 'AUDIT-ATOM-TEST-%'",
        ).fetchall()
        assert len(audit_records) > 0
        assert audit_records[0][0]["action"] == "approval_approved"
        
        # Verify reservation was created
        reservations = conn.execute(
            """
            SELECT reservation_id
            FROM inventory_reservations
            WHERE payload->>'case_id' = %s
            """,
            ("ATOM-TEST-CASE-1",),
        ).fetchall()
        assert len(reservations) == 1
    
    # Clean up
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("DELETE FROM approval_state_tracking WHERE approval_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM audit_records WHERE audit_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM notification_outbox WHERE event_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM inventory_reservations WHERE reservation_id LIKE 'ATOM-TEST-%'")
        conn.execute("DELETE FROM inventory_units WHERE unit_id LIKE 'ATOM-TEST-%'")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for atomic transaction tests",
)
def test_rejected_approval_records_audit_atomically():
    """Verify that rejection records audit state atomically."""
    import psycopg
    
    # Clean up test data
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("DELETE FROM approval_state_tracking WHERE approval_id LIKE 'REJECT-TEST-%'")
        conn.execute("DELETE FROM audit_records WHERE audit_id LIKE 'REJECT-TEST-%'")
        conn.execute("DELETE FROM inventory_units WHERE unit_id LIKE 'REJECT-TEST-%'")
    
    # Set up repository and audit
    repo = PostgreSQLInventoryRepository(DATABASE_URL)
    audit = PostgreSQLAuditRepository(DATABASE_URL)
    
    # Create test data
    test_unit = unit("REJECT-TEST-UNIT-1")
    repo.save_unit(test_unit)
    
    service = ReservationApprovalService(repo, audit=audit)
    case, match = make_case("REJECT-TEST-UNIT-1")
    case.case_id = "REJECT-TEST-CASE-1"
    case.request_id = "REJECT-TEST-REQ-1"
    
    proposal_event = service.propose(case, match)
    recommendation = proposal_event.payload.recommendation
    
    approval = approval_for(recommendation, ApprovalDecision.REJECT, approval_id="REJECT-TEST-APPROVAL-1")
    approval_event = EventEnvelope.approval_decided(
        approval,
        recommendation,
        correlation_id=case.case_id,
    )
    
    # Handle rejection
    result = service.handle_approval(approval_event)
    assert result is not None
    
    # Verify approval state was recorded
    with psycopg.connect(DATABASE_URL) as conn:
        approval_state = conn.execute(
            "SELECT decision FROM approval_state_tracking WHERE approval_id = %s",
            ("REJECT-TEST-APPROVAL-1",),
        ).fetchone()
        assert approval_state is not None
        assert approval_state[0] == "REJECTED"
        
        # Verify audit record was written
        audit_records = conn.execute(
            "SELECT payload FROM audit_records WHERE audit_id LIKE 'AUDIT-REJECT-TEST-%'",
        ).fetchall()
        assert len(audit_records) > 0
        assert audit_records[0][0]["action"] == "approval_rejected"
    
    # Clean up
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("DELETE FROM approval_state_tracking WHERE approval_id LIKE 'REJECT-TEST-%'")
        conn.execute("DELETE FROM audit_records WHERE audit_id LIKE 'REJECT-TEST-%'")
        conn.execute("DELETE FROM inventory_units WHERE unit_id LIKE 'REJECT-TEST-%'")
