import importlib.util
import hashlib
import hmac
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
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL-backed workflow tests",
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
    spec = importlib.util.spec_from_file_location("bloodnet_workflow_api", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.match_svc.match_request.__globals__["score_donors"] = lambda donors: [0.99 for _ in donors]
    return module


def set_identity(module, identity):
    override = lambda: identity
    module.app.dependency_overrides[get_identity] = override
    module.match_svc.app.dependency_overrides[get_identity] = override
    module.swarm_svc.app.dependency_overrides[get_identity] = override


def test_capability_readiness_is_authenticated_and_non_secret():
    module = load_api()
    client = TestClient(module.app)
    set_identity(module, Identity("REGION-ADMIN", "regional_admin", region_id="pune"))

    response = client.get("/api/v1/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["capabilities"]["graph_analysis"]["code_ready"] is True
    assert "BLOODNET_DATABASE_URL" not in response.text


def test_signed_messaging_ingress_is_idempotent(monkeypatch):
    module = load_api()
    client = TestClient(module.app)
    secret = "test-webhook-secret"
    monkeypatch.setenv("BLOODNET_MESSAGING_WEBHOOK_SECRET", secret)
    event_id = uuid4().hex
    payload = {
        "provider": "whatsapp",
        "provider_event_id": event_id,
        "sender_id": "provider-contact-token",
        "message": "Need 1 O positive RBC unit routine",
        "hospital_id": "HOSP-001",
    }
    signed_value = f"whatsapp:{event_id}:provider-contact-token:{payload['message']}:HOSP-001"
    signature = hmac.new(secret.encode(), signed_value.encode(), hashlib.sha256).hexdigest()

    first = client.post("/api/v1/inbound/messaging", json=payload, headers={"X-BloodNet-Signature": f"sha256={signature}"})
    second = client.post("/api/v1/inbound/messaging", json=payload, headers={"X-BloodNet-Signature": f"sha256={signature}"})

    assert first.status_code == 200
    assert first.json()["status"] == "ingested"
    persisted_request = module.match_svc.workflow_store.requests[f"MSG-whatsapp-{event_id}"]
    assert persisted_request.get("region")
    assert second.json() == {"status": "already_ingested", "request_id": f"MSG-whatsapp-{event_id}"}
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    activity = client.get("/api/v1/inbound/messaging/events").json()["events"]
    persisted = next(item for item in activity if item["provider_event_id"] == event_id)
    assert persisted["status"] == "ingested"
    assert persisted["request_id"] == f"MSG-whatsapp-{event_id}"
    assert "provider-contact-token" not in str(persisted)
    assert payload["message"] not in str(persisted)


def workflow_payload(request_id="REQ-FULL"):
    now = datetime.now(timezone.utc)
    donors = [
        {"donor_id": donor_id, "blood_group": "A+", "geo": {"lat": 18.52, "lng": 73.85}, "contact_tokens": ["demo"], "consent_scopes": ["contactable"]}
        for donor_id in ("DONOR-001", "DONOR-002", "DONOR-003")
    ]
    return seed_units({
        "request": {"request_id": request_id, "group": "A+", "component": "RBC", "qty": 3, "hospital_id": "HOSP-001", "urgency": "Critical", "required_by": (now + timedelta(hours=4)).isoformat(), "source_channel": "hospital-web", "verification_state": "verified", "status": "open"},
        "hospital": {"hospital_id": "HOSP-001", "name": "City Hospital", "geo": {"lat": 18.5204, "lng": 73.8567}, "tier": "tertiary", "affiliated_banks": ["BANK-001"]},
        "banks": [{"bank_id": "BANK-001", "name": "Central Blood Bank", "geo": {"lat": 18.521, "lng": 73.857}, "licence_id": "LIC-001"}],
        "units": [{"unit_id": f"UNIT-{request_id}", "bank_id": "BANK-001", "group": "A+", "component": "RBC", "collected_at": now.isoformat(), "expires_at": (now + timedelta(days=30)).isoformat(), "status": "available"}],
        "donors": donors,
        "eligible_donor_ids": [donor["donor_id"] for donor in donors],
    })


def test_full_hospital_to_donor_fulfillment():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-FULL-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    matched = client.post("/match-svc/api/v1/match", json=workflow_payload(request_id))
    assert matched.status_code == 200
    case_id = matched.json()["case"]["case_id"]
    rec_id = matched.json()["recommendation"]["rec_id"]

    set_identity(module, Identity("BANK-001-ADMIN", "bank_admin", bank_id="BANK-001"))
    approved = client.post(f"/match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/approve", json={"rationale": "Emergency approval"})
    assert approved.status_code == 200
    assert approved.json()["case"]["reservation_state"] == "reserved"

    for donor_id in ("DONOR-001", "DONOR-002"):
        set_identity(module, Identity(donor_id, "donor"))
        opportunities = client.get("/swarm-svc/api/v1/opportunities?donor_id=ignored")
        assert opportunities.status_code == 200
        assert opportunities.json()["opportunities"]
        opportunity = opportunities.json()["opportunities"][0]
        response = client.post(f"/swarm-svc/api/v1/outreach/{opportunity['outreach_id']}/response", json={"donor_id": "forged", "response": "accept"})
        assert response.status_code == 200

    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill").status_code == 409
    set_identity(module, Identity("BANK-001-ADMIN", "bank_admin", bank_id="BANK-001"))
    reservation_id = approved.json()["case"]["reservation_ids"][0]
    assert client.post(f"/match-svc/api/v1/inventory/reservations/{reservation_id}/consume").status_code == 200
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    outcome = client.post(f"/match-svc/api/v1/cases/{case_id}/outcomes", json={"event_id": "receipt", "inventory_units_received": 1, "donor_units_received": 2, "reference": "Test receipt"})
    assert outcome.status_code == 200, outcome.text
    fulfilled = client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill")
    assert fulfilled.status_code == 200
    assert fulfilled.json()["case"]["outcome"] == "fulfilled"

    hospital = client.get(f"/match-svc/api/v1/cases/{case_id}")
    assert hospital.json()["case"]["outcome"] == "fulfilled"
    assert "ranked_donors" not in hospital.text
    assert "DONOR-001" not in hospital.text


def test_rejection_keeps_inventory_and_shortfall_unchanged():
    module = load_api()
    client = TestClient(module.app)
    request_id = f"REQ-REJECT-{uuid4().hex}"
    set_identity(module, Identity("HOSP-001-COORD", "hospital_coordinator", hospital_id="HOSP-001"))
    matched = client.post("/match-svc/api/v1/match", json=workflow_payload(request_id))
    case_id = matched.json()["case"]["case_id"]
    rec_id = matched.json()["recommendation"]["rec_id"]

    set_identity(module, Identity("BANK-001-ADMIN", "bank_admin", bank_id="BANK-001"))
    rejected = client.post(f"/match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/reject", json={"rationale": "Inventory not approved"})
    assert rejected.status_code == 200
    assert rejected.json()["case"]["reservation_state"] == "rejected"
    assert rejected.json()["case"]["units_from_donors_remaining"] == 2
    inventory = client.get(f"/match-svc/api/v1/inventory/UNIT-{request_id}")
    assert inventory.status_code == 200
    assert inventory.json()["status"] == "available"


def test_authorization_failures_are_enforced_at_backend_boundary():
    module = load_api()
    client = TestClient(module.app)
    set_identity(module, Identity("DONOR-001", "donor"))
    assert client.get("/match-svc/api/v1/inventory").status_code == 403
    assert client.get("/match-svc/api/v1/audit").status_code == 403
    assert client.get("/match-svc/api/v1/regional/forecast").status_code == 403
    assert client.post("/match-svc/api/v1/cases/unknown/fulfill").status_code == 403
