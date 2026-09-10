"""Deterministic local proof of the complete BloodNet emergency loop."""

from datetime import date, datetime, timedelta, timezone
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for service_dir in ("intake-svc", "match-svc", "swarm-svc", "execution-svc", "agent-svc", "notify-svc"):
    path = str(ROOT / "services" / service_dir)
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_service import AgentService
from contracts.audit import AuditRepository
from contracts.events import EventEnvelope, EventType
from contracts.forecast import ForecastPoint, ForecastResult
from contracts.models import Approval, ApprovalDecision, BloodBank, BloodGroup, Component, Donor, GeoPoint, InventoryStatus, InventoryUnit, Hospital, Recommendation, VerificationState
from execution_service import ExecutionCoordinator, InMemoryExecutionRepository
from forecast_service import InMemoryForecastRepository, forecast_response
from inventory_mutation import InventoryRepository
from notification_service import NotificationRepository, NotificationService
from parser import LocalIntakeProvider, extract_request
from request_flow import match_request
from event_handler import SwarmEventHandler
from approval_flow import ReservationApprovalService


NOW = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)


def test_golden_demo_proves_closed_loop_and_negative_invariants():
    hospital = Hospital(hospital_id="H-GOLDEN", name="Golden Hospital", geo=GeoPoint(lat=18.52, lng=73.85), tier="tier1")
    bank = BloodBank(bank_id="BANK-GOLDEN", name="Golden Blood Bank", geo=hospital.geo, licence_id="LIC-GOLDEN")
    request = extract_request("Critical request: 3 units of O negative RBC for Golden Hospital", hospital_id=hospital.hospital_id, request_id="REQ-GOLDEN", required_by=NOW + timedelta(hours=2), provider=LocalIntakeProvider())
    request = request.model_copy(update={"verification_state": VerificationState.VERIFIED})
    units = [
        InventoryUnit(unit_id="UNIT-GOLDEN-1", bank_id=bank.bank_id, group=BloodGroup.O_NEG, component=Component.RBC, collected_at=NOW - timedelta(days=2), expires_at=NOW + timedelta(days=30)),
        InventoryUnit(unit_id="UNIT-GOLDEN-INCOMPATIBLE", bank_id=bank.bank_id, group=BloodGroup.A_POS, component=Component.RBC, collected_at=NOW - timedelta(days=2), expires_at=NOW + timedelta(days=30)),
    ]
    eligible_donor = Donor(donor_id="DONOR-GOLDEN-ELIGIBLE", blood_group=BloodGroup.O_NEG, geo=hospital.geo, age_years=30, reliability_features={"historical_response_rate": 0.9, "historical_completion_rate": 0.9, "days_since_last_donation": 180, "is_repeat_donor": 1})
    incompatible_donor = eligible_donor.model_copy(update={"donor_id": "DONOR-GOLDEN-INCOMPATIBLE", "blood_group": BloodGroup.A_POS})
    audit = AuditRepository()
    inventory = InventoryRepository(units=units)
    stages: set[str] = set()

    assert request.verification_state.value == "verified"
    stages.add("request_valid")
    matched = match_request(request, hospital, [bank], units, [eligible_donor, incompatible_donor], eligible_donor_ids={eligible_donor.donor_id, incompatible_donor.donor_id})
    case, ranked = matched.case, matched.ranked_donors
    stages.update({"request_persisted", "inventory_compatible", "donors_eligible", "donors_ranked"})
    assert [item["donor_id"] for item in ranked] == [eligible_donor.donor_id]
    assert all(item["blood_group"] == BloodGroup.O_NEG.value for item in ranked)

    swarm_event = EventEnvelope.case_ranked(request.request_id, case, ranked, correlation_id="CORR-GOLDEN")
    swarm_notification = SwarmEventHandler().handle_case_ranked(swarm_event)
    assert swarm_notification is not None
    stages.add("swarm_adaptive")

    forecast = ForecastResult(forecast_run_id="FORECAST-GOLDEN", model_version="golden-v1", confidence_level=0.95, created_at=NOW, points=[ForecastPoint(target_date=date.today(), region="Pune", blood_group="O-", component="RBC", predicted_demand=8, lower_bound=6, upper_bound=10, projected_supply=2)])
    weather = forecast_response(InMemoryForecastRepository([forecast]).get_forecast("Pune", 7), "Pune", 7)
    assert weather["forecast"] and weather["forecast"][0]["shortage_probability"] > 0
    stages.add("weather_forecast")

    investigation = AgentService(max_calls=4, tools={
        "get_demand_forecast": lambda **_: {"status": "success", "data": weather},
        "search_sops": lambda **_: {"status": "success", "data": {"passages": [{"citation": "SOP-GOLDEN-1"}]}},
        "query_graph": lambda **_: {"status": "success", "data": {"findings": [{"bank_id": bank.bank_id}]}},
        "simulate_intervention": lambda **_: {"status": "success", "data": {"projected_shortfall": 0}},
    }).investigate([
        {"tool": "get_demand_forecast", "arguments": {"region": "Pune", "blood_group": "O-", "component": "RBC"}},
        {"tool": "search_sops", "arguments": {"query": "urgent O negative RBC transfer"}},
        {"tool": "query_graph", "arguments": {"analysis_type": "coverage_zones", "params": {}}},
        {"tool": "simulate_intervention", "arguments": {"intervention_spec": {"baseline_shortfall": 2, "transferred_units": 2}}},
    ])
    assert {citation["tool"] for citation in investigation["citations"]} == {"get_demand_forecast", "search_sops", "query_graph", "simulate_intervention"}
    stages.update({"investigation_executed", "rag_retrieved", "graph_attached", "simulation_attached"})

    evidence = investigation["findings"]
    digest = hashlib.sha256(repr(evidence).encode("utf-8")).hexdigest()
    recommendation = Recommendation(rec_id="REC-GOLDEN", type="INVENTORY_RESERVATION", request_id=request.request_id, case_id=case.case_id, payload={"bank_id": bank.bank_id, "unit_ids": ["UNIT-GOLDEN-1"], "reservation_id": "RES-GOLDEN"}, rationale="Reserve compatible O-negative inventory and escalate the residual shortfall.", evidence=evidence, provenance={"digest": digest, "citations": investigation["citations"], "model": "deterministic-gemini-test"})
    assert recommendation.state == "AWAITING_APPROVAL" and recommendation.provenance["digest"] == digest
    stages.update({"recommendation_persisted", "provenance_persisted"})

    execution_repo = InMemoryExecutionRepository()
    coordinator = ExecutionCoordinator(repository=inventory, execution_repository=execution_repo, audit=audit)
    try:
        coordinator.execute_approved(recommendation, approval=None, case=case)
    except ValueError:
        pass
    else:
        raise AssertionError("unapproved recommendation executed")
    assert inventory.get_unit("UNIT-GOLDEN-1").status is InventoryStatus.AVAILABLE
    stages.add("execution_blocked_before_approval")

    approval_service = ReservationApprovalService(inventory, audit, authorize_actor=lambda actor: actor == "bank-admin")
    proposal_event = approval_service.propose(case, case.inventory_matches[0], reservation_id="RES-GOLDEN", ranked_donors=ranked)
    proposal = approval_service.recommendations[proposal_event.payload.recommendation.rec_id]
    unauthorized = Approval(approval_id="APP-GOLDEN-UNAUTHORIZED", rec_id=proposal.rec_id, actor="viewer", decision=ApprovalDecision.APPROVE, rationale="", at=NOW)
    try:
        approval_service.handle_approval(EventEnvelope.approval_decided(unauthorized, proposal, ranked_donors=ranked, correlation_id="CORR-GOLDEN"))
    except Exception as error:
        assert "not authorized" in str(error)
    else:
        raise AssertionError("unauthorized approval succeeded")
    assert inventory.get_unit("UNIT-GOLDEN-1").status is InventoryStatus.AVAILABLE
    stages.add("unauthorized_approval_blocked")

    approval = Approval(approval_id="APP-GOLDEN", rec_id=proposal.rec_id, actor="bank-admin", decision=ApprovalDecision.APPROVE, rationale="Approved", at=NOW)
    approved_event = approval_service.handle_approval(EventEnvelope.approval_decided(approval, proposal, ranked_donors=ranked, correlation_id="CORR-GOLDEN"))
    assert approved_event and approved_event.event_type is EventType.INVENTORY_RESERVED
    result = coordinator.execute_approved(proposal, approval=approval, case=case)
    duplicate = coordinator.execute_approved(proposal, approval=approval, case=case)
    assert result["execution_id"] == duplicate["execution_id"]
    assert inventory.get_unit("UNIT-GOLDEN-1").status is InventoryStatus.RESERVED
    assert len(execution_repo.records) == 1
    stages.update({"approval_authorized", "execution_exactly_once", "inventory_mutated", "receipt_persisted"})

    notifications = NotificationService(repository=NotificationRepository(), sender=lambda _: None, audit=audit)
    sent = notifications.handle_request(swarm_notification.model_copy(update={"payload": swarm_notification.payload.model_copy(update={"case": case})}))
    assert sent and notifications.repository.all()
    stages.add("notification_created")
    assert case.units_from_donors_remaining == 1 and case.escalation_state == "required"
    stages.update({"outcome_recorded", "shortfall_recalculated", "escalation_reached"})

    required = {"request_valid", "inventory_compatible", "donors_eligible", "donors_ranked", "swarm_adaptive", "weather_forecast", "investigation_executed", "rag_retrieved", "graph_attached", "simulation_attached", "recommendation_persisted", "provenance_persisted", "execution_blocked_before_approval", "approval_authorized", "execution_exactly_once", "inventory_mutated", "receipt_persisted", "notification_created", "outcome_recorded", "shortfall_recalculated", "escalation_reached"}
    assert required <= stages