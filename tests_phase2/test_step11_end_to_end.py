from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for service_dir in ("intake-svc", "match-svc", "swarm-svc", "execution-svc"):
    path = str(ROOT / "services" / service_dir)
    if path not in sys.path:
        sys.path.insert(0, path)

from contracts.events import EventEnvelope
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
    Recommendation,
)
from event_handler import SwarmEventHandler
from execution_service import ExecutionCoordinator
from inventory_mutation import InventoryRepository
from parser import LocalIntakeProvider, extract_request
from request_flow import match_request


def test_step11_request_to_escalation_closed_loop():
    now = datetime.now(timezone.utc)
    hospital = Hospital(
        hospital_id="H-E2E",
        name="E2E Hospital",
        geo=GeoPoint(lat=18.52, lng=73.85),
        tier="tier1",
    )
    request = extract_request(
        "Need 3 units of A positive RBC urgently",
        hospital_id=hospital.hospital_id,
        request_id="REQ-E2E-STEP11",
        required_by=now + timedelta(hours=2),
        provider=LocalIntakeProvider(),
    )
    bank = BloodBank(
        bank_id="BANK-E2E",
        name="E2E Bank",
        geo=hospital.geo,
        licence_id="LIC-E2E",
    )
    unit = InventoryUnit(
        unit_id="UNIT-E2E-1",
        bank_id=bank.bank_id,
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=30),
    )
    donor = Donor(
        donor_id="DONOR-E2E-1",
        blood_group=BloodGroup.A_POS,
        geo=hospital.geo,
        age_years=30,
        reliability_features={
            "historical_response_rate": 0.9,
            "historical_completion_rate": 0.9,
            "days_since_last_donation": 180,
            "is_repeat_donor": 1,
            "distance_to_bank_km": 2,
        },
    )

    matched = match_request(
        request,
        hospital,
        [bank],
        [unit],
        [donor],
        eligible_donor_ids={donor.donor_id},
    )
    assert matched.case.units_from_inventory == 1
    assert matched.case.units_from_donors_remaining == 2

    recommendation = Recommendation(
        rec_id="REC-E2E-STEP11",
        type="RESERVE_INVENTORY",
        request_id=request.request_id,
        case_id=matched.case.case_id,
        payload={
            "bank_id": bank.bank_id,
            "unit_ids": [unit.unit_id],
            "reservation_id": "RES-E2E-STEP11",
        },
        rationale="Reserve the available unit before escalating the residual shortfall.",
        state="APPROVED",
    )
    approval = Approval(
        approval_id="APP-E2E-STEP11",
        rec_id=recommendation.rec_id,
        actor="e2e-admin",
        decision=ApprovalDecision.APPROVE,
        rationale="Approved for deterministic execution.",
        at=now,
    )

    result = ExecutionCoordinator(
        repository=InventoryRepository(units=[unit])
    ).execute_approved(
        recommendation,
        approval=approval,
        case=matched.case,
    )

    assert result["remaining_shortfall"] == 1
    assert result["escalation_required"] is True

    event = EventEnvelope.case_ranked(
        request.request_id,
        matched.case,
        matched.ranked_donors,
        correlation_id="CORR-E2E-STEP11",
    )
    notification = SwarmEventHandler().handle_case_ranked(event)

    assert notification is not None
    assert notification.payload.donor_ids == [donor.donor_id]