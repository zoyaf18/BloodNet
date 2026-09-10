"""Executable local demonstration of the BloodNet event-driven hot path."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "contracts", ROOT / "services" / "match-svc", ROOT / "services" / "swarm-svc", ROOT / "ml"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
NOTIFY_SVC = ROOT / "services" / "notify-svc"
if str(NOTIFY_SVC) not in sys.path:
    sys.path.insert(0, str(NOTIFY_SVC))

from contracts.event_bus import LocalEventBus
from contracts.audit import AuditRepository
from contracts.events import EventType, EventEnvelope
from contracts.models import (
    Approval,
    ApprovalDecision,
    BloodBank,
    BloodGroup,
    Component,
    Donor,
    GeoPoint,
    Hospital,
    InventoryUnit,
    OutreachResponse,
    Request,
    Urgency,
    VerificationState,
)
from contracts.observability import configure_logging, log_event
from request_flow import match_request
from event_handler import SwarmEventHandler
from approval_flow import ReservationApprovalService
from inventory_mutation import InventoryRepository
from notification_service import NotificationService
from fulfillment_flow import FulfillmentService

# event_handler.py is loaded under the simple local import name below.



def build_demo_data():
    now = datetime.now(timezone.utc)
    hospital = Hospital(
        hospital_id="H-DEMO",
        name="Demo Hospital",
        geo=GeoPoint(lat=18.5204, lng=73.8567),
        tier="tier1",
    )
    request = Request(
        request_id="REQ-DEMO-001",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=3,
        hospital_id=hospital.hospital_id,
        urgency=Urgency.CRITICAL,
        required_by=now + timedelta(hours=2),
        source_channel="local-demo",
        verification_state=VerificationState.VERIFIED,
    )
    bank = BloodBank(
        bank_id="BANK-DEMO",
        name="Demo Blood Bank",
        geo=GeoPoint(lat=18.525, lng=73.855),
        licence_id="LIC-DEMO",
    )
    units = [
        InventoryUnit(
            unit_id="UNIT-DEMO-001",
            bank_id=bank.bank_id,
            group=BloodGroup.O_POS,
            component=Component.RBC,
            collected_at=now - timedelta(days=5),
            expires_at=now + timedelta(days=30),
        )
    ]
    donors = [
        Donor(
            donor_id="D-DEMO-001",
            blood_group=BloodGroup.A_POS,
            geo=hospital.geo,
            consent_scopes=["contactable"],
            reliability_features={
                "historical_response_rate": 0.95,
                "historical_completion_rate": 0.95,
                "days_since_last_donation": 120,
                "age": 30,
                "is_repeat_donor": 1,
                "distance_to_bank_km": 2,
            },
        ),
        Donor(
            donor_id="D-DEMO-002",
            blood_group=BloodGroup.O_POS,
            geo=hospital.geo,
            consent_scopes=["contactable"],
            reliability_features={
                "historical_response_rate": 0.80,
                "historical_completion_rate": 0.85,
                "days_since_last_donation": 180,
                "age": 27,
                "is_repeat_donor": 1,
                "distance_to_bank_km": 4,
            },
        ),
    ]
    return request, hospital, [bank], units, donors


def main() -> None:
    logger = configure_logging()
    request, hospital, banks, units, donors = build_demo_data()

    bus = LocalEventBus()
    swarm = SwarmEventHandler()
    audit = AuditRepository()
    approval_service = ReservationApprovalService(InventoryRepository(units=units), audit)
    notification_service = NotificationService(audit=audit)
    fulfillment_service = FulfillmentService(approval_service.repository, audit)
    ranked_donors_by_case: dict[str, list[dict]] = {}

    def dispatch_swarm_after_reservation(event: EventEnvelope):
        case = event.payload.case
        ranked_donors = ranked_donors_by_case.get(case.case_id, [])
        return swarm.handle_case_ranked(
            EventEnvelope.case_ranked(
                case.request_id,
                case,
                ranked_donors,
                correlation_id=event.correlation_id,
            )
        )

    def dispatch_swarm_without_inventory_proposal(event: EventEnvelope):
        case = event.payload.case
        ranked_donors_by_case[case.case_id] = event.payload.ranked_donors
        if not any(match.selected_unit_ids for match in case.inventory_matches):
            return swarm.handle_case_ranked(event)
        return None

    bus.subscribe(EventType.CASE_RANKED, dispatch_swarm_without_inventory_proposal)
    bus.subscribe(EventType.INVENTORY_RESERVED, fulfillment_service.handle_inventory_reserved)
    bus.subscribe(EventType.INVENTORY_RESERVED, dispatch_swarm_after_reservation)
    bus.subscribe(EventType.NOTIFICATIONS_REQUESTED, notification_service.handle_request)

    def demo_donor_acceptance(event: EventEnvelope):
        notification = event.payload.notification
        case = fulfillment_service.cases[notification.case_id]
        return EventEnvelope.donor_response(
            case,
            notification.donor_id,
            OutreachResponse.ACCEPT,
            correlation_id=event.correlation_id,
        )

    bus.subscribe(EventType.NOTIFICATION_SENT, demo_donor_acceptance)
    bus.subscribe(EventType.DONOR_RESPONSE_RECEIVED, fulfillment_service.handle_donor_response)

    def propose_inventory(event: EventEnvelope):
        case = event.payload.case
        selected_match = next(
            (match for match in case.inventory_matches if match.selected_unit_ids),
            None,
        )
        if selected_match is None:
            return None
        return approval_service.propose(case, selected_match)

    def demo_approval(event: EventEnvelope):
        recommendation = event.payload.recommendation
        approval = Approval(
            approval_id=f"APPROVAL-{recommendation.rec_id}",
            rec_id=recommendation.rec_id,
            actor="local-demo-admin",
            decision=ApprovalDecision.APPROVE,
            rationale="Local demo approval",
            at=datetime.now(timezone.utc),
        )
        return EventEnvelope.approval_decided(
            approval,
            recommendation,
            correlation_id=event.correlation_id,
        )

    bus.subscribe(EventType.CASE_RANKED, propose_inventory)
    bus.subscribe(EventType.RESERVATION_PROPOSED, demo_approval)
    bus.subscribe(EventType.APPROVAL_DECIDED, approval_service.handle_approval)

    def match_handler(event: EventEnvelope):
        result = match_request(
            event.payload.request,
            hospital,
            banks,
            units,
            donors,
            eligible_donor_ids={d.donor_id for d in donors},
        )
        return EventEnvelope.case_ranked(
            request_id=request.request_id,
            case=result.case,
            ranked_donors=result.ranked_donors,
            correlation_id=event.correlation_id,
        )

    bus.subscribe(EventType.REQUEST_CREATED, match_handler)

    created = EventEnvelope.request_created(request)
    case_events = bus.publish(created)
    pending_events = case_events
    while pending_events:
        next_events = []
        for pending_event in pending_events:
            next_events.extend(bus.publish(pending_event))
        pending_events = next_events

    # Deliberate duplicate delivery: idempotency must suppress it.
    duplicate_outputs = bus.publish(created)

    print("\n=== BloodNet Local Event Flow ===")
    print(f"request_id={request.request_id}")
    print(f"request_event_id={created.event_id}")
    if case_events:
        case = case_events[0].payload.case
        print(f"case_id={case.case_id}")
        print(f"units_from_inventory={case.units_from_inventory}")
        print(f"units_remaining={case.units_from_donors_remaining}")
        print(f"ranked_donors={len(case_events[0].payload.ranked_donors)}")
        print(f"reservation_state={case.reservation_state.value}")
        print(f"outcome={case.outcome.value}")
        print(f"audit_records={len(audit.all())}")
    print(f"duplicate_event_outputs={len(duplicate_outputs)}")


if __name__ == "__main__":
    main()
