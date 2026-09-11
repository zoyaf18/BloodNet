"""Database-backed blood-bank and authorization regression flows."""
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from contracts.auth import Identity, get_identity
from contracts.models import InventoryUnit, InventoryStatus


@pytest.fixture(scope="module")
def api_module():
    spec = importlib.util.spec_from_file_location("review_unified_api", Path(__file__).parents[1] / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    import psycopg
    with psycopg.connect(os.environ["BLOODNET_DATABASE_URL"]) as connection:
        connection.execute("DELETE FROM approval_state_tracking WHERE outbox_event_id IN (SELECT event_id FROM notification_outbox WHERE request_id LIKE 'REVIEW-REQ-%') OR case_id IN (SELECT case_id FROM workflow_cases WHERE payload->>'request_id' LIKE 'REVIEW-REQ-%')")
        connection.execute("DELETE FROM notification_outbox WHERE request_id LIKE 'REVIEW-REQ-%'")


@pytest.fixture
def bank(api_module):
    identity = Identity("review-bank", "bank_admin", bank_id="REVIEW-BANK-1")
    api_module.match_svc.app.dependency_overrides[get_identity] = lambda: identity
    yield api_module.match_svc, TestClient(api_module.app), identity
    api_module.match_svc.app.dependency_overrides.clear()


def stock(bank_id="REVIEW-BANK-1", **updates):
    now = datetime.now(timezone.utc)
    return InventoryUnit(unit_id=f"REVIEW-{uuid4().hex}", bank_id=bank_id, group="AB-", component="RBC",
                         collected_at=now-timedelta(days=1), expires_at=now+timedelta(days=30), **updates)


def sync(client, units, event=None):
    return client.post("/match-svc/api/v1/integrations/blood-bank/inventory", json={
        "source_system": "review", "source_event_id": event or uuid4().hex,
        "units": [unit.model_dump(mode="json") for unit in units],
    })


def test_sync_create_retry_conflict_and_discard(bank):
    module, client, _ = bank
    unit = stock()
    event = uuid4().hex
    assert sync(client, [unit], event).status_code == 200
    assert sync(client, [unit], event).json()["status"] == "already_synchronized"
    changed = unit.model_copy(update={"status": InventoryStatus.DISCARDED})
    assert sync(client, [changed], event).status_code == 409
    assert sync(client, [changed]).status_code == 200
    assert sync(client, [unit]).status_code == 409
    assert module.workflow_store.repository.get_unit(unit.unit_id).status.value == "discarded"


def test_sync_cannot_take_over_other_bank_unit(bank):
    module, client, _ = bank
    unit = stock("REVIEW-BANK-2")
    module.workflow_store.repository.save_unit(unit)
    forged = unit.model_copy(update={"bank_id": "REVIEW-BANK-1"})
    assert sync(client, [forged]).status_code == 403
    assert module.workflow_store.repository.get_unit(unit.unit_id).bank_id == "REVIEW-BANK-2"


@pytest.mark.parametrize("status", ["reserved", "issued", "in_transit"])
def test_sync_cannot_reset_allocated_stock(bank, status):
    module, client, _ = bank
    unit = stock(status=status)
    module.workflow_store.repository.save_unit(unit)
    assert sync(client, [unit.model_copy(update={"status": InventoryStatus.AVAILABLE})]).status_code == 409
    assert module.workflow_store.repository.get_unit(unit.unit_id).status.value == status


def test_sync_batch_rollback_and_duplicate_validation(bank):
    module, client, _ = bank
    unit = stock()
    assert sync(client, [unit, unit]).status_code == 422
    expired = stock()
    expired.expires_at = datetime.now(timezone.utc)-timedelta(hours=1)
    assert sync(client, [unit, expired]).status_code == 422
    from inventory_mutation import InventoryUnitNotFoundError
    with pytest.raises(InventoryUnitNotFoundError):
        module.workflow_store.repository.get_unit(unit.unit_id)


@pytest.mark.parametrize("path", ["inventory", "reservations", "inventory/expiry-risk"])
def test_missing_bank_scope_fails_closed(bank, path):
    _, client, identity = bank
    identity.bank_id = None
    assert client.get(f"/match-svc/api/v1/{path}").status_code == 403


def match_payload(unit):
    now = datetime.now(timezone.utc)
    return {"request": {"request_id": f"REVIEW-REQ-{uuid4().hex}", "hospital_id": "REVIEW-HOSP", "region": "Pune",
                        "group": "AB-", "component": "RBC", "qty": 1, "urgency": "Critical",
                        "required_by": (now+timedelta(hours=1)).isoformat(), "source_channel": "review"},
            "hospital": {"hospital_id": "REVIEW-HOSP", "name": "Review Hospital", "geo": {"lat": 18.52,"lng":73.85}, "tier": "tertiary"},
            "banks": [{"bank_id": unit.bank_id, "name": "Review Bank", "geo": {"lat":18.52,"lng":73.85}, "licence_id":"REVIEW"}],
            "units": [unit.model_dump(mode="json")], "donors": [], "eligible_donor_ids": [], "eligibility_records": {}}


@pytest.mark.parametrize("role", ["donor", "bank_admin", "auditor"])
def test_match_rejects_non_requester_roles(bank, role):
    module, client, identity = bank
    identity.role = role
    assert client.post("/match-svc/api/v1/match", json=match_payload(stock())).status_code == 403


def test_match_retry_and_authoritative_inventory(bank):
    module, client, identity = bank
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    unit = stock(status="discarded")
    module.workflow_store.repository.save_unit(unit)
    payload = match_payload(unit.model_copy(update={"status": InventoryStatus.AVAILABLE}))
    first = client.post("/match-svc/api/v1/match", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["case"]["units_from_inventory"] == 0
    again = client.post("/match-svc/api/v1/match", json=payload)
    assert again.status_code == 200, again.text
    assert again.json()["case"]["case_id"] == first.json()["case"]["case_id"]
    payload["request"]["qty"] = 2
    assert client.post("/match-svc/api/v1/match", json=payload).status_code == 409
    assert module.workflow_store.repository.get_unit(unit.unit_id).status.value == "discarded"


def test_bank_approval_has_case_link_and_cancellation_releases_stock(bank):
    module, client, identity = bank
    unit = stock()
    assert sync(client, [unit]).status_code == 200
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    payload = match_payload(unit)
    payload["request"]["qty"] = 2
    matched = client.post("/match-svc/api/v1/match", json=payload)
    assert matched.status_code == 200, matched.text
    data = matched.json()
    case_id = data["case"]["case_id"]
    rec_id = data["recommendation"]["rec_id"]
    assert data["recommendation"]["case_id"] == case_id
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill").status_code == 409
    identity.role = "bank_admin"
    approved = client.post(f"/match-svc/api/v1/cases/{case_id}/reservations/{rec_id}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["recommendations"][0]["state"] == "APPROVED"
    assert module.workflow_store.repository.get_unit(unit.unit_id).status.value == "reserved"
    pending = client.get("/match-svc/api/v1/reservations/pending")
    assert pending.status_code == 200, pending.text
    assert rec_id not in {item["rec_id"] for item in pending.json()["recommendations"]}
    assert client.get("/match-svc/api/v1/reservations").json()["reservations"]
    identity.role = "hospital_coordinator"
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/cancel", json={"reason":"Review cancellation"}).status_code == 200
    assert module.workflow_store.repository.get_unit(unit.unit_id).status.value == "available"
    assert client.post(f"/match-svc/api/v1/cases/{case_id}/fulfill").status_code == 409


def test_multibank_case_requires_each_bank_approval(bank):
    module, client, identity = bank
    units = [stock(), stock("REVIEW-BANK-2")]
    for unit in units:
        module.workflow_store.repository.save_unit(unit)
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    payload = match_payload(units[0])
    payload["request"]["qty"] = 2
    payload["units"] = [unit.model_dump(mode="json") for unit in units]
    payload["banks"].append({**payload["banks"][0], "bank_id": "REVIEW-BANK-2"})
    response = client.post("/match-svc/api/v1/match", json=payload)
    assert response.status_code == 200, response.text
    case = response.json()["case"]
    rec_ids = case["reservation_recommendation_ids"]
    assert len(rec_ids) == 2
    identity.role = "bank_admin"
    for index, rec_id in enumerate(rec_ids):
        rec = module.workflow_store.approval_service.recommendations[rec_id]
        identity.bank_id = "UNRELATED-BANK"
        endpoint = f"/match-svc/api/v1/cases/{case['case_id']}/reservations/{rec_id}/approve"
        assert client.post(endpoint, json={}).status_code in {403, 404}
        identity.bank_id = rec.reservation_proposal.bank_id
        approved = client.post(endpoint, json={})
        assert approved.status_code == 200, approved.text
        current = module.workflow_store.approval_service.cases[case["case_id"]]
        assert current.reservation_state == ("awaiting_approval" if index == 0 else "reserved")
    assert all(module.workflow_store.repository.get_unit(unit.unit_id).status == InventoryStatus.RESERVED for unit in units)


def test_sync_rejects_timezone_free_dates(bank):
    _, client, _ = bank
    unit = stock()
    unit.expires_at = unit.expires_at.replace(tzinfo=None)
    assert sync(client, [unit]).status_code == 422


def test_sync_rejects_units_not_yet_collected(bank):
    _, client, _ = bank
    unit = stock()
    unit.collected_at = datetime.now(timezone.utc) + timedelta(days=1)
    assert sync(client, [unit]).status_code == 422


def test_no_inventory_mobilization_requires_approval_and_executes_after_regional_approval(bank, monkeypatch):
    module, client, identity = bank
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    payload = match_payload(stock())
    payload["units"] = []
    result = client.post("/match-svc/api/v1/match", json=payload)
    assert result.status_code == 200, result.text
    data = result.json()
    recommendation = data["recommendation"]
    assert recommendation["type"] == "MOBILIZE_DONORS"
    assert recommendation["state"] == "AWAITING_APPROVAL"
    status = module.workflow_store.get_swarm_status(data["case"]["case_id"])
    assert status["status"] == "awaiting_approval"
    assert status["donors_contacted"] == []
    identity.role = "auditor"
    assert client.post(f"/match-svc/api/v1/recommendations/{recommendation['rec_id']}/approve", json={}).status_code == 403
    observed_states = []

    def execute_approved(current, *, approval, case):
        observed_states.append(current.state)
        return {"status": "executed", "escalation_required": False}

    monkeypatch.setattr(module.workflow_store.execution, "execute_approved", execute_approved)
    identity.role = "regional_admin"
    approved = client.post(
        f"/match-svc/api/v1/recommendations/{recommendation['rec_id']}/approve",
        json={},
    )
    assert approved.status_code == 200, approved.text
    assert observed_states == ["APPROVED"]
    assert approved.json()["recommendation"]["state"] == "EXECUTED"
    assert approved.json()["approval"]["status"] == "EXECUTED"


def test_server_selected_donors_survive_empty_browser_screening(bank, monkeypatch):
    module, client, identity = bank
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    monkeypatch.setattr(module.auth_repo, "list_contactable_donor_profiles", lambda: [{
        "user_id": "REVIEW-CONSENTED-DONOR", "blood_group": "AB-", "lat": 18.52,
        "lng": 73.85, "last_donation_at": None, "region_id": "Pune",
    }])
    payload = match_payload(stock())
    payload["units"] = []
    response = client.post("/match-svc/api/v1/match", json=payload)
    assert response.status_code == 200, response.text
    assert [donor["donor_id"] for donor in response.json()["ranked_donors"]] == ["REVIEW-CONSENTED-DONOR"]


@pytest.mark.parametrize('excluded', [
    {'region_id':'Mumbai'}, {'lat':19.076,'lng':72.8777}, {'blood_group':'O+'},
])
def test_matching_excludes_eligible_but_out_of_scope_donors(bank, monkeypatch, excluded):
    module,client,identity=bank
    identity.role='hospital_coordinator'; identity.hospital_id='REVIEW-HOSP'; identity.region_id='Pune'
    donor={'user_id':'REVIEW-EXCLUDED','blood_group':'AB-','region_id':'Pune','lat':18.52,'lng':73.85,'last_donation_at':None,**excluded}
    monkeypatch.setattr(module.auth_repo,'list_contactable_donor_profiles',lambda:[donor])
    payload=match_payload(stock()); payload['units']=[]
    response=client.post('/match-svc/api/v1/match',json=payload)
    assert response.status_code==200,response.text
    assert response.json()['ranked_donors']==[]
    assert module.workflow_store.get_swarm_status(response.json()["case"]["case_id"])["status"] == "awaiting_approval"


def test_production_match_ignores_client_clinical_and_supply_assertions(bank, monkeypatch):
    module, client, identity = bank
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    payload = match_payload(stock())
    payload["donors"] = [{"donor_id": "CLIENT-INELIGIBLE", "blood_group": "AB-",
                           "geo": {"lat": 18.52, "lng": 73.85}}]
    payload["eligible_donor_ids"] = ["CLIENT-INELIGIBLE"]
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setattr(module, "api_hospital_context", lambda identity: {
        "hospital": {**payload["hospital"], "region": "pune"}, "banks": [], "units": []})
    monkeypatch.setattr(module.auth_repo, "list_contactable_donor_profiles", lambda: [])
    response = client.post("/match-svc/api/v1/match", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["ranked_donors"] == []
    assert response.json()["case"]["units_from_inventory"] == 0
    assert response.json()["recommendation"]["state"] == "AWAITING_APPROVAL"


@pytest.mark.parametrize("qty", [0, -1, 1001])
def test_invalid_request_quantity_does_not_create_case(bank, qty):
    module, client, identity = bank
    identity.role = "hospital_coordinator"
    identity.hospital_id = "REVIEW-HOSP"
    identity.region_id = "Pune"
    payload = match_payload(stock())
    payload["request"]["qty"] = qty
    assert client.post("/match-svc/api/v1/match", json=payload).status_code == 422
    assert payload["request"]["request_id"] not in module.workflow_store.requests
