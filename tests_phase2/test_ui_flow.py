"""API-level verification for the multi-role demo journey."""

import importlib.util
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from contracts.auth import Identity, get_identity
from tests_phase2.workflow_fixtures import seed_hospital, seed_units

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL-backed UI flow tests",
)


def load_api():
    assert DATABASE_URL is not None
    seed_hospital()
    with psycopg.connect(DATABASE_URL) as connection:
        for table in (
            "donor_responses",
            "notifications",
            "inventory_reservations",
            "inventory_units",
            "workflow_recommendations",
            "workflow_cases",
            "workflow_requests",
        ):
            connection.execute(f"DELETE FROM {table}")
    spec = importlib.util.spec_from_file_location("bloodnet_ui_flow_api", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.match_svc.match_request.__globals__["score_donors"] = lambda donors: [0.99 for _ in donors]
    return module


def payload(request_id: str):
    now = datetime.now(timezone.utc)
    return seed_units({
        "request": {
            "request_id": request_id,
            "group": "A+",
            "component": "RBC",
            "qty": 3,
            "hospital_id": "HOSP-001",
            "urgency": "Critical",
            "required_by": (now + timedelta(hours=4)).isoformat(),
            "source_channel": "hospital-web",
            "verification_state": "verified",
            "status": "open",
        },
        "hospital": {"hospital_id": "HOSP-001", "name": "City Hospital", "geo": {"lat": 18.5204, "lng": 73.8567}, "tier": "tertiary", "affiliated_banks": ["BANK-001"]},
        "banks": [{"bank_id": "BANK-001", "name": "Central Blood Bank", "geo": {"lat": 18.521, "lng": 73.857}, "licence_id": "LIC-001"}],
        "units": [{"unit_id": f"UNIT-{request_id}", "bank_id": "BANK-001", "group": "A+", "component": "RBC", "collected_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat(), "status": "available"}],
        "donors": [{"donor_id": "DONOR-001", "blood_group": "A+", "geo": {"lat": 18.52, "lng": 73.85}, "contact_tokens": ["demo"], "consent_scopes": ["contactable"]}],
            "donors": [
                {"donor_id": "DONOR-001", "blood_group": "A+", "geo": {"lat": 18.52, "lng": 73.85}, "contact_tokens": ["demo"], "consent_scopes": ["contactable"]},
                {"donor_id": "DONOR-002", "blood_group": "A+", "geo": {"lat": 18.52, "lng": 73.85}, "contact_tokens": ["demo"], "consent_scopes": ["contactable"]},
            ],
            "eligible_donor_ids": ["DONOR-001", "DONOR-002"],
    })


def create_case(client, request_id):
    response = client.post("/match-svc/api/v1/match", json=payload(request_id))
    assert response.status_code == 200
    return response.json()


def test_hospital_inbound_request_ingestion_is_scoped_and_idempotent():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-INGEST-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))

    response = client.post("/api/v1/requests/ingest", json={
        "raw_text": "Need 2 units of O positive blood urgently",
        "hospital_id": "HOSP-001",
        "source_channel": "hospital-system",
        "request_id": request_id,
        "provider": "local",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "ingested"
    assert module.match_svc.workflow_store.requests[request_id]["qty"] == 2

    duplicate = client.post("/api/v1/requests/ingest", json={
        "raw_text": "Need 9 units of A negative blood",
        "hospital_id": "HOSP-001",
        "request_id": request_id,
        "provider": "local",
    })
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "already_ingested"

    forbidden = client.post("/api/v1/requests/ingest", json={
        "raw_text": "Need 1 unit of O positive blood",
        "hospital_id": "OTHER-HOSPITAL",
        "provider": "local",
    })
    assert forbidden.status_code == 403


def set_identity(module, identity):
    module.app.dependency_overrides[get_identity] = lambda: identity
    module.match_svc.app.dependency_overrides[get_identity] = lambda: identity
    module.swarm_svc.app.dependency_overrides[get_identity] = lambda: identity


def test_complete_hospital_bank_donor_journey():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-UI-APPROVE-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    scenario = payload(request_id)
    scenario["request"].update(group="O+", qty=4)
    scenario["units"][0]["group"] = "O+"
    scenario["units"].append({**scenario["units"][0], "unit_id": f"UNIT-SECOND-{request_id}"})
    for donor in scenario["donors"]:
        donor["blood_group"] = "O+"
    seed_units(scenario)
    matched_response = client.post("/match-svc/api/v1/match", json=scenario)
    assert matched_response.status_code == 200, matched_response.text
    matched = matched_response.json()
    case_id = matched["case"]["case_id"]
    rec_id = matched["recommendation"]["rec_id"]

    hospital_before = client.get(f"/match-svc/api/v1/cases/{case_id}").json()
    assert hospital_before["case"]["units_from_inventory"] == 2
    assert "ranked_donors" not in hospital_before
    assert "recommendations" not in hospital_before
    updates = module.match_svc.case_realtime_hub.subscribe_to_case(case_id)

    set_identity(module, Identity("BANK-001-ADMIN", "bank_admin", bank_id="BANK-001"))
    approved = client.post(f"/match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/approve", json={"rationale": "Emergency approval"})
    assert approved.status_code == 200
    assert approved.json()["case"]["reservation_state"] == "reserved"
    assert approved.json()["swarm"]["status"] == "initiated"
    assert updates.get(timeout=1)["case"]["reservation_state"] == "reserved"

    set_identity(module, Identity("DONOR-001", "donor"))
    opportunity = client.get("/swarm-svc/api/v1/opportunities?donor_id=DONOR-001")
    assert opportunity.status_code == 200
    assert "requester_id" not in opportunity.text
    outreach_id = opportunity.json()["opportunities"][0]["outreach_id"]
    response = client.post(f"/swarm-svc/api/v1/outreach/{outreach_id}/response", json={"donor_id": "forged", "response": "accept"})
    assert response.json()["status"] == "accepted"
    assert updates.get(timeout=1)["case"]["units_from_donors_fulfilled"] == 1

    set_identity(module, Identity("DONOR-002", "donor"))
    # A separate Cloud Run worker may never have seen the case in memory.
    module.match_svc.workflow_store.approval_service.cases.pop(case_id, None)
    second_opportunity = client.get("/swarm-svc/api/v1/opportunities?donor_id=forged").json()["opportunities"][0]
    second_response = client.post(f"/swarm-svc/api/v1/outreach/{second_opportunity['outreach_id']}/response", json={"donor_id": "forged", "response": "accept"})
    assert second_response.json()["status"] == "accepted"
    assert updates.get(timeout=1)["case"]["units_from_donors_fulfilled"] == 2
    replay = client.post(f"/swarm-svc/api/v1/outreach/{second_opportunity['outreach_id']}/response", json={"donor_id": "forged", "response": "accept"})
    assert replay.json()["status"] == "accepted"
    assert updates.get(timeout=1)["case"]["units_from_donors_fulfilled"] == 2
    refreshed = client.get("/swarm-svc/api/v1/opportunities").json()["opportunities"]
    assert refreshed[0]["status"] == "accepted"

    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill").status_code == 409
    assert client.get(f"/match-svc/api/v1/cases/{case_id}").json()["case"]["outcome"] != "fulfilled"
    set_identity(module, Identity("BANK-001-ADMIN", "bank_admin", bank_id="BANK-001"))
    reservation_id = approved.json()["case"]["reservation_ids"][0]
    assert client.post(f"/match-svc/api/v1/inventory/reservations/{reservation_id}/consume").status_code == 200
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    receipt = {"event_id": "partial", "inventory_units_received": 2, "donor_units_received": 0, "reference": "Review receipt partial"}
    received = client.post(f"/match-svc/api/v1/cases/{case_id}/outcomes", json=receipt)
    assert received.status_code == 200, received.text
    assert received.json()["case"]["confirmed_inventory_units"] == 2
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/outcomes", json=receipt).json()["status"] == "already_recorded"
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill").status_code == 409
    receipt.update(event_id="complete", donor_units_received=2)
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/outcomes", json=receipt).status_code == 200
    set_identity(module, Identity("DONOR-002", "donor"))
    contradiction = client.post(f"/swarm-svc/api/v1/outreach/{second_opportunity['outreach_id']}/response", json={"donor_id": "forged", "response": "decline"})
    assert contradiction.status_code == 409
    assert client.get("/swarm-svc/api/v1/opportunities").json()["opportunities"][0]["status"] == "accepted"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    while not updates.empty():
        updates.get_nowait()
    hospital = client.get(f"/match-svc/api/v1/cases/{case_id}").json()
    fulfilled = client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill")
    assert fulfilled.status_code == 200
    assert updates.get(timeout=1)["case"]["outcome"] == "fulfilled"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    hospital_after = client.get(f"/match-svc/api/v1/cases/{case_id}").json()
    assert hospital["case"]["units_from_donors_fulfilled"] == 2
    assert hospital["case"]["outcome"] == "partially_fulfilled"
    assert hospital["case"]["units_from_donors_remaining"] == 0
    assert "ranked_donors" not in hospital
    assert hospital_after["case"]["outcome"] == "fulfilled"
    assert hospital_after["case"]["units_from_donors_fulfilled"] == 2
    assert "donor_id" not in str(hospital_after)
    restored = module.match_svc.WorkflowStore()
    assert restored.approval_service.cases[case_id].outcome == "fulfilled"
    assert restored.approval_service.cases[case_id].confirmed_donor_units == 2
    actions = {record.action for record in restored.audit.all() if record.case_id == case_id}
    assert {"hospital_receipt_confirmed", "hospital_fulfillment_confirmed", "inventory_allocation_completed"} <= actions


def test_rejection_keeps_inventory_available_and_shortfall_intact():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-UI-REJECT-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    matched = create_case(client, request_id)
    case_id = matched["case"]["case_id"]
    rec_id = matched["recommendation"]["rec_id"]
    set_identity(module, Identity("BANK-001-ADMIN", "bank_admin", bank_id="BANK-001"))
    rejected = client.post(f"/match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/reject", json={"rationale": "Not approved"})
    assert rejected.status_code == 200
    assert rejected.json()["case"]["reservation_state"] == "rejected"
    assert rejected.json()["case"]["units_from_donors_remaining"] == 2
    unit = client.get(f"/match-svc/api/v1/inventory/UNIT-{request_id}").json()
    assert unit["status"] == "available"


def test_hospital_can_cancel_case_and_cancellation_is_audited():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-UI-CANCEL-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    matched = create_case(client, request_id)
    case_id = matched["case"]["case_id"]
    updates = module.match_svc.case_realtime_hub.subscribe_to_case(case_id)

    set_identity(module, Identity("DONOR-003", "donor"))
    forbidden = client.post(f"/match-svc/api/v1/cases/{case_id}/cancel", json={"reason": "Duplicate request"})
    assert forbidden.status_code == 403

    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    cancelled = client.post(f"/match-svc/api/v1/cases/{case_id}/cancel", json={"reason": "Procedure rescheduled"})
    assert cancelled.status_code == 200
    assert cancelled.json()["case"]["outcome"] == "cancelled"
    assert cancelled.json()["case"]["escalation_state"] == "cancelled"
    assert updates.get(timeout=1)["case"]["outcome"] == "cancelled"

    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    closed = client.post(f"/match-svc/api/v1/cases/{case_id}/cancel", json={"reason": "Second attempt"})
    assert closed.status_code == 409

    set_identity(module, Identity("AUDITOR-001", "auditor"))
    audit = client.get(f"/match-svc/api/v1/audit?case_id={case_id}")
    assert audit.status_code == 200
    assert any(event["action"] == "case_cancelled" and event.get("details", {}).get("reason") == "Procedure rescheduled" for event in audit.json()["events"])


def test_hospital_can_escalate_open_case_and_closed_cases_are_protected():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-UI-ESCALATE-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    matched = create_case(client, request_id)
    case_id = matched["case"]["case_id"]
    updates = module.match_svc.case_realtime_hub.subscribe_to_case(case_id)

    set_identity(module, Identity("DONOR-004", "donor"))
    forbidden = client.post(f"/match-svc/api/v1/cases/{case_id}/escalate", json={"reason": "Need another donor round"})
    assert forbidden.status_code == 403

    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    escalated = client.post(f"/match-svc/api/v1/cases/{case_id}/escalate", json={"reason": "Insufficient response after round one"})
    assert escalated.status_code == 200
    assert escalated.json()["case"]["escalation_state"] == "required"
    assert updates.get(timeout=1)["case"]["escalation_state"] == "required"

    cancelled = client.post(f"/match-svc/api/v1/cases/{case_id}/cancel", json={"reason": "Request withdrawn"})
    assert cancelled.status_code == 200
    closed = client.post(f"/match-svc/api/v1/cases/{case_id}/escalate", json={"reason": "Should be rejected"})
    assert closed.status_code == 409
