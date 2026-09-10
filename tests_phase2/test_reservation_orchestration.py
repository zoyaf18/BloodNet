from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATCH_SVC = ROOT / "services" / "match-svc"
SWARM_SVC = ROOT / "services" / "swarm-svc"
for path in (ROOT, MATCH_SVC, SWARM_SVC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from contracts.event_bus import LocalEventBus
from contracts.events import EventEnvelope, EventType
from contracts.models import (
    Approval,
    ApprovalDecision,
    BloodGroup,
    Case,
    Component,
    InventoryMatch,
    InventoryStatus,
    InventoryUnit,
    Recommendation,
)
from approval_flow import ReservationApprovalService
from inventory_mutation import InventoryRepository
from reservation_orchestrator import ReservationOrchestrator
from event_handler import SwarmEventHandler


NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def make_case() -> Case:
    return Case(
        case_id="CASE-FLOW",
        request_id="REQ-FLOW",
        inventory_matches=[
            InventoryMatch(
                bank_id="BANK-1",
                units_available=1,
                selected_unit_ids=["UNIT-1"],
                distance_km=1.0,
                eta_min=2.0,
            )
        ],
        units_from_inventory=1,
        units_from_donors_remaining=2,
    )


def make_unit() -> InventoryUnit:
    return InventoryUnit(
        unit_id="UNIT-1",
        bank_id="BANK-1",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=NOW - timedelta(days=2),
        expires_at=NOW + timedelta(days=30),
    )


def make_approval(recommendation: Recommendation, decision: ApprovalDecision) -> Approval:
    return Approval(
        approval_id=f"APP-{decision.value.upper()}",
        rec_id=recommendation.rec_id,
        actor="admin-1",
        decision=decision,
        rationale="reviewed",
        at=NOW,
    )


def test_match_proposal_approval_reservation_then_swarm_shortfall():
    repository = InventoryRepository(units=[make_unit()])
    approval_service = ReservationApprovalService(repository)
    orchestrator = ReservationOrchestrator(approval_service)
    swarm = SwarmEventHandler()
    ranked_donors = [
        {"donor_id": "D1", "success_probability": 0.99},
        {"donor_id": "D2", "success_probability": 0.99},
        {"donor_id": "D3", "success_probability": 0.99},
    ]

    case_ranked = EventEnvelope.case_ranked(
        "REQ-FLOW", make_case(), ranked_donors, correlation_id="CORR-1"
    )
    proposal = orchestrator.handle_case_ranked(case_ranked)

    assert proposal is not None
    assert proposal.event_type == EventType.RESERVATION_PROPOSED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.AVAILABLE
    assert proposal.payload.ranked_donors == ranked_donors

    approval_event = EventEnvelope.approval_decided(
        make_approval(proposal.payload.recommendation, ApprovalDecision.APPROVE),
        proposal.payload.recommendation,
        ranked_donors=proposal.payload.ranked_donors,
        correlation_id=proposal.correlation_id,
    )
    after_approval = orchestrator.handle_approval(approval_event)

    assert after_approval is not None
    assert after_approval.event_type == EventType.CASE_RANKED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.RESERVED
    assert after_approval.payload.case.reservation_state.value == "reserved"
    assert after_approval.payload.case.units_from_inventory == 1
    assert after_approval.payload.case.units_from_donors_remaining == 2
    assert after_approval.payload.ranked_donors == ranked_donors

    swarm_output = swarm.handle_case_ranked(after_approval)
    assert swarm_output is not None
    assert swarm_output.event_type == EventType.NOTIFICATIONS_REQUESTED
    assert len(swarm_output.payload.donor_ids) <= len(ranked_donors)
    assert swarm_output.payload.case.units_from_donors_remaining == 2


def test_rejected_proposal_never_mutates_inventory_or_releases_swarm_flow():
    repository = InventoryRepository(units=[make_unit()])
    approval_service = ReservationApprovalService(repository)
    orchestrator = ReservationOrchestrator(approval_service)

    proposal = orchestrator.handle_case_ranked(
        EventEnvelope.case_ranked(
            "REQ-REJECT",
            make_case().model_copy(update={"case_id": "CASE-REJECT", "request_id": "REQ-REJECT"}),
            [{"donor_id": "D1", "success_probability": 0.9}],
            correlation_id="CORR-REJECT",
        )
    )
    assert proposal is not None

    rejected = orchestrator.handle_approval(
        EventEnvelope.approval_decided(
            make_approval(proposal.payload.recommendation, ApprovalDecision.REJECT),
            proposal.payload.recommendation,
            ranked_donors=proposal.payload.ranked_donors,
            correlation_id=proposal.correlation_id,
        )
    )

    assert rejected is not None
    assert rejected.event_type == EventType.RESERVATION_REJECTED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.AVAILABLE
    assert len(repository.get_reservations()) == 0


def test_rejection_e2e_keeps_inventory_and_shortfall_intact():
    repository = InventoryRepository(units=[make_unit()])
    approval_service = ReservationApprovalService(repository)
    orchestrator = ReservationOrchestrator(approval_service)
    case = make_case().model_copy(update={"case_id": "CASE-E2E-REJECT", "request_id": "REQ-E2E-REJECT"})

    proposal = orchestrator.handle_case_ranked(
        EventEnvelope.case_ranked(
            case.request_id,
            case,
            [{"donor_id": "D1", "success_probability": 0.95}],
            correlation_id="CORR-E2E-REJECT",
        )
    )
    assert proposal is not None

    rejected = orchestrator.handle_approval(
        EventEnvelope.approval_decided(
            make_approval(proposal.payload.recommendation, ApprovalDecision.REJECT),
            proposal.payload.recommendation,
            ranked_donors=proposal.payload.ranked_donors,
            correlation_id=proposal.correlation_id,
        )
    )

    assert rejected is not None
    assert rejected.event_type == EventType.RESERVATION_REJECTED
    assert case.reservation_state.value == "rejected"
    assert case.units_from_inventory == 1
    assert case.units_from_donors_remaining == 2
    assert repository.get_unit("UNIT-1").status == InventoryStatus.AVAILABLE
    assert len(repository.get_reservations()) == 0


def test_duplicate_approval_event_delivery_cannot_reserve_twice():
    repository = InventoryRepository(units=[make_unit()])
    approval_service = ReservationApprovalService(repository)
    orchestrator = ReservationOrchestrator(approval_service)

    proposal = orchestrator.handle_case_ranked(
        EventEnvelope.case_ranked(
            "REQ-IDEMP",
            make_case().model_copy(update={"case_id": "CASE-IDEMP", "request_id": "REQ-IDEMP"}),
            [{"donor_id": "D1", "success_probability": 0.99}],
            correlation_id="CORR-IDEMP",
        )
    )
    approval = make_approval(proposal.payload.recommendation, ApprovalDecision.APPROVE)
    event = EventEnvelope.approval_decided(
        approval,
        proposal.payload.recommendation,
        ranked_donors=proposal.payload.ranked_donors,
        correlation_id=proposal.correlation_id,
    )

    first = orchestrator.handle_approval(event)
    second = orchestrator.handle_approval(
        EventEnvelope.approval_decided(
            approval,
            proposal.payload.recommendation,
            ranked_donors=proposal.payload.ranked_donors,
            correlation_id=proposal.correlation_id,
        )
    )

    assert first is not None
    assert second is None
    assert len(repository.get_reservations()) == 1
    assert repository.get_unit("UNIT-1").status == InventoryStatus.RESERVED


def test_event_bus_duplicate_delivery_is_idempotent_across_orchestrator():
    repository = InventoryRepository(units=[make_unit()])
    orchestrator = ReservationOrchestrator(ReservationApprovalService(repository))
    bus = LocalEventBus()
    bus.subscribe(EventType.CASE_RANKED, orchestrator.handle_case_ranked)

    event = EventEnvelope.case_ranked(
        "REQ-BUS",
        make_case().model_copy(update={"case_id": "CASE-BUS", "request_id": "REQ-BUS"}),
        [{"donor_id": "D1", "success_probability": 0.99}],
        correlation_id="CORR-BUS",
    )

    first = bus.publish(event)
    second = bus.publish(event)

    assert len(first) == 1
    assert first[0].event_type == EventType.RESERVATION_PROPOSED
    assert second == []
    assert repository.get_unit("UNIT-1").status == InventoryStatus.AVAILABLE
