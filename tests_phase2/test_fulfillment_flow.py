from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATCH_SVC = ROOT / "services" / "match-svc"
if str(MATCH_SVC) not in sys.path:
    sys.path.insert(0, str(MATCH_SVC))

from contracts.audit import AuditRepository
from contracts.events import EventEnvelope, EventType
from contracts.models import (
    BloodGroup,
    Case,
    Component,
    InventoryStatus,
    InventoryUnit,
    OutreachResponse,
    Recommendation,
)
from fulfillment_flow import FulfillmentService
from inventory_mutation import InventoryRepository, reserve_inventory
from processed_event_repository import InMemoryProcessedEventRepository


NOW = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)


def test_approved_inventory_and_donor_acceptance_complete_case_with_audit():
    repository = InventoryRepository(
        units=[
            InventoryUnit(
                unit_id="UNIT-1",
                bank_id="BANK-1",
                group=BloodGroup.A_POS,
                component=Component.RBC,
                collected_at=NOW - timedelta(days=2),
                expires_at=NOW + timedelta(days=30),
            )
        ]
    )
    reservation = reserve_inventory(
        repository,
        case_id="CASE-1",
        request_id="REQ-1",
        bank_id="BANK-1",
        unit_ids=["UNIT-1"],
        reservation_id="RES-1",
    )
    case = Case(
        case_id="CASE-1",
        request_id="REQ-1",
        units_from_inventory=1,
        units_from_donors_remaining=1,
    )
    audit = AuditRepository()
    service = FulfillmentService(repository, audit)

    consumed = service.handle_inventory_reserved(
        EventEnvelope.inventory_reserved(
            case,
            # A recommendation is not needed by fulfillment, but the event
            # contract carries it upstream and this fixture tests the boundary.
            _recommendation(),
            reservation,
            correlation_id=case.case_id,
        )
    )
    completed = service.handle_donor_response(
        EventEnvelope.donor_response(
            case,
            "DONOR-1",
            OutreachResponse.ACCEPT,
            correlation_id=case.case_id,
        )
    )

    assert consumed.event_type == EventType.INVENTORY_CONSUMED
    assert repository.get_unit("UNIT-1").status == InventoryStatus.ISSUED
    assert completed is not None
    assert completed.event_type == EventType.FULFILLMENT_COMPLETED
    assert case.outcome.value == "fulfilled"
    assert case.units_from_donors_fulfilled == 1
    assert {record.action for record in audit.all()} == {
        "inventory_consumed",
        "donor_units_fulfilled",
        "case_fulfilled",
    }


def test_duplicate_donor_acceptance_does_not_complete_twice():
    case = Case(case_id="CASE-2", request_id="REQ-2", units_from_donors_remaining=1)
    service = FulfillmentService(InventoryRepository())
    first = service.handle_donor_response(
        EventEnvelope.donor_response(
            case, "DONOR-1", OutreachResponse.ACCEPT, correlation_id=case.case_id
        )
    )
    second = service.handle_donor_response(
        EventEnvelope.donor_response(
            case, "DONOR-1", OutreachResponse.ACCEPT, correlation_id=case.case_id
        )
    )

    assert first is not None
    assert second is None
    assert len(service.audit.all()) == 2


def test_duplicate_inventory_event_is_consumed_only_once():
    repository = InventoryRepository(
        units=[
            InventoryUnit(
                unit_id="UNIT-DUPLICATE-EVENT",
                bank_id="BANK-1",
                group=BloodGroup.A_POS,
                component=Component.RBC,
                collected_at=NOW - timedelta(days=2),
                expires_at=NOW + timedelta(days=30),
            )
        ]
    )
    reservation = reserve_inventory(
        repository,
        case_id="CASE-DUPLICATE-EVENT",
        request_id="REQ-DUPLICATE-EVENT",
        bank_id="BANK-1",
        unit_ids=["UNIT-DUPLICATE-EVENT"],
        reservation_id="RES-DUPLICATE-EVENT",
    )
    case = Case(case_id="CASE-DUPLICATE-EVENT", request_id="REQ-DUPLICATE-EVENT")
    event = EventEnvelope.inventory_reserved(
        case,
        _recommendation(),
        reservation,
        correlation_id=case.case_id,
    ).model_copy(update={"event_id": "EVENT-DUPLICATE-EVENT"})
    service = FulfillmentService(
        repository,
        processed_event_repository=InMemoryProcessedEventRepository(),
    )

    service.handle_inventory_reserved(event)
    duplicate_result = service.handle_inventory_reserved(event)

    assert duplicate_result.event_type == EventType.INVENTORY_CONSUMED
    assert repository.get_unit("UNIT-DUPLICATE-EVENT").status == InventoryStatus.ISSUED
    assert len(service.audit.all()) == 1


def test_donor_response_persists_outcome_and_escalation():
    class ResponseStore:
        def __init__(self):
            self.saved = []
            self.processed = []

        def save_response(self, outreach_id, donor_id, response, responded_at):
            self.saved.append((outreach_id, donor_id, response))

        def mark_processed(self, outreach_id, donor_id):
            self.processed.append((outreach_id, donor_id))

    persisted_cases = []
    persisted_recommendations = []
    response_store = ResponseStore()
    case = Case(case_id="CASE-3", request_id="REQ-3", units_from_donors_remaining=2)
    service = FulfillmentService(
        InventoryRepository(),
        donor_response_store=response_store,
        case_persister=persisted_cases.append,
        escalation_persister=persisted_recommendations.append,
    )

    result = service.handle_donor_response(
        EventEnvelope.donor_response(
            case,
            "DONOR-1",
            OutreachResponse.DECLINE,
            outreach_id="OUTREACH-3",
            correlation_id=case.case_id,
        )
    )
    assert result is None
    assert response_store.saved == [("OUTREACH-3", "DONOR-1", "decline")]
    assert response_store.processed == [("OUTREACH-3", "DONOR-1")]

    result = service.handle_donor_response(
        EventEnvelope.donor_response(
            case,
            "DONOR-2",
            OutreachResponse.ACCEPT,
            outreach_id="OUTREACH-4",
            correlation_id=case.case_id,
        )
    )
    assert result is None
    assert case.units_from_donors_fulfilled == 1
    assert case.outcome == "partially_fulfilled"
    assert case.escalation_state == "required"
    assert persisted_cases[-1] is case
    assert isinstance(persisted_recommendations[-1], Recommendation)
    assert persisted_recommendations[-1].state == "AWAITING_APPROVAL"


def test_donor_decline_or_no_reply_keeps_case_open_and_requests_next_round():
    persisted_recommendations = []
    case = Case(case_id="CASE-4", request_id="REQ-4", units_from_donors_remaining=2)
    service = FulfillmentService(
        InventoryRepository(),
        case_persister=lambda updated: None,
        escalation_persister=persisted_recommendations.append,
    )

    decline = service.handle_donor_response(
        EventEnvelope.donor_response(
            case,
            "DONOR-1",
            OutreachResponse.DECLINE,
            outreach_id="OUTREACH-DECLINE",
            correlation_id=case.case_id,
        )
    )
    no_reply = service.handle_donor_response(
        EventEnvelope.donor_response(
            case,
            "DONOR-2",
            OutreachResponse.NO_REPLY,
            outreach_id="OUTREACH-NO-REPLY",
            correlation_id=case.case_id,
        )
    )

    assert decline is None
    assert no_reply is None
    assert case.outcome == "partially_fulfilled"
    assert case.escalation_state == "required"
    assert len(persisted_recommendations) == 2
    assert all(isinstance(item, Recommendation) for item in persisted_recommendations)


def _recommendation():
    from contracts.models import Recommendation

    return Recommendation(rec_id="REC-1", type="INVENTORY_RESERVATION", payload={})