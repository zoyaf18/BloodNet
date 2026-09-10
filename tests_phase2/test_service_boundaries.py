from datetime import datetime, timezone
from uuid import uuid4
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.models import BloodGroup, Component, Identity, Request, RoleType, Urgency

ROOT = Path(__file__).resolve().parents[1]
requires_database = pytest.mark.skipif(
    not os.environ.get("BLOODNET_DATABASE_URL"),
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL-backed match flow tests",
)


def load_service_module(name: str, path: Path):
    service_dir = str(path.parent)
    sys.path.insert(0, service_dir)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(service_dir)


def make_request(qty: int = 3) -> Request:
    return Request(
        request_id=f"REQ-{uuid4().hex}",
        region="TEST-REGION",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=qty,
        hospital_id="H1",
        urgency=Urgency.CRITICAL,
        required_by=datetime.now(timezone.utc),
        source_channel="test",
    )


def with_regional_identity(module):
    module.app.dependency_overrides[module.get_identity] = lambda: Identity(
        subject_id="TEST-REGIONAL",
        email="regional@example.test",
        display_name="Test Regional Admin",
        role=RoleType.REGIONAL_ADMIN,
        organization_id="TEST-REGION",
        region_id="TEST-REGION",
    )
    return TestClient(module.app)


def test_match_service_imports_and_health():
    module = load_service_module("match_main", ROOT / "services/match-svc/main.py")
    response = TestClient(module.app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_swarm_service_imports_and_health():
    module = load_service_module("swarm_main", ROOT / "services/swarm-svc/main.py")
    response = TestClient(module.app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_swarm_sizes_against_inventory_shortfall():
    module = load_service_module("swarm_main_shortfall", ROOT / "services/swarm-svc/main.py")
    response = with_regional_identity(module).post(
        "/api/v1/escalate",
        json={
            "request": make_request(3).model_dump(mode="json"),
            "available_banks": [],
            "available_donors": [
                {
                    "donor_id": f"D{i}",
                    "blood_group": "A+",
                    "geo": {"lat": 18.52, "lng": 73.85},
                    "contact_tokens": [f"t{i}"],
                    "consent_scopes": ["contactable"],
                }
                for i in range(10)
            ],
            "units_from_inventory": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["units_remaining"] == 1
    assert body["target_units"] == 1


def test_swarm_skips_donor_mobilization_when_inventory_fully_covers_request():
    module = load_service_module("swarm_main_full", ROOT / "services/swarm-svc/main.py")
    response = with_regional_identity(module).post(
        "/api/v1/escalate",
        json={
            "request": make_request(3).model_dump(mode="json"),
            "available_banks": [],
            "available_donors": [],
            "units_from_inventory": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["units_remaining"] == 0
    assert body["target_units"] == 0
    assert body["action"] == "proceed_to_inventory_approval"


@requires_database
def test_match_request_creates_inventory_aware_case_and_ranked_donors(monkeypatch):
    module = load_service_module("match_main_flow", ROOT / "services/match-svc/main.py")
    client = with_regional_identity(module)

    # Keep this service-boundary test independent of the serialized LightGBM
    # artifact: verify that only eligible + compatible donors reach the scorer.
    monkeypatch.setitem(
        module.match_request.__globals__,
        "score_donors",
        lambda donors: [0.91 if d.donor_id == "D1" else 0.42 for d in donors],
    )

    request = make_request(3)
    hospital = {
        "hospital_id": "H1",
        "name": "Test Hospital",
        "geo": {"lat": 18.5204, "lng": 73.8567},
        "tier": "tier1",
    }
    banks = [
        {
            "bank_id": "B1",
            "name": "Nearby Bank",
            "geo": {"lat": 18.521, "lng": 73.857},
            "licence_id": "LIC-1",
        }
    ]
    from datetime import timedelta
    from contracts.models import InventoryStatus
    now = datetime.now(timezone.utc)
    units = [
        {
            "unit_id": "U1",
            "bank_id": "B1",
            "group": "A+",
            "component": "RBC",
            "collected_at": now - timedelta(days=5),
            "expires_at": now + timedelta(days=20),
            "status": InventoryStatus.AVAILABLE.value,
        }
    ]
    donors = [
        {
            "donor_id": "D1",
            "blood_group": "A+",
            "geo": {"lat": 18.52, "lng": 73.85},
            "contact_tokens": ["t1"],
            "consent_scopes": ["contactable"],
        },
        {
            "donor_id": "D2",
            "blood_group": "O-",
            "geo": {"lat": 18.52, "lng": 73.85},
            "contact_tokens": ["t2"],
            "consent_scopes": ["contactable"],
        },
        {
            "donor_id": "D3",
            "blood_group": "B+",
            "geo": {"lat": 18.52, "lng": 73.85},
            "contact_tokens": ["t3"],
            "consent_scopes": ["contactable"],
        },
    ]

    from contracts.models import InventoryUnit
    for unit in units:
        module.workflow_store.repository.save_unit(InventoryUnit.model_validate(unit))

    response = client.post(
        "/api/v1/match",
        json={
            "request": request.model_dump(mode="json"),
            "hospital": hospital,
            "banks": banks,
            "units": [u | {"collected_at": u["collected_at"].isoformat(), "expires_at": u["expires_at"].isoformat()} for u in units],
            "donors": donors,
            "eligible_donor_ids": ["D1", "D2"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["case"]["units_from_inventory"] == 1
    assert body["case"]["units_from_donors_remaining"] == 2
    assert body["case"]["request_id"] == request.request_id
    assert [d["donor_id"] for d in body["ranked_donors"]] == ["D1", "D2"]
    assert body["ranked_donors"][0]["success_probability"] == 0.91


@requires_database
def test_match_request_never_scores_ineligible_or_incompatible_donors(monkeypatch):
    module = load_service_module("match_main_gate", ROOT / "services/match-svc/main.py")
    client = with_regional_identity(module)
    scored_ids = []

    def fake_score(donors):
        scored_ids.extend(d.donor_id for d in donors)
        return [0.5] * len(donors)

    monkeypatch.setitem(module.match_request.__globals__, "score_donors", fake_score)

    response = client.post(
        "/api/v1/match",
        json={
            "request": make_request(1).model_dump(mode="json"),
            "hospital": {
                "hospital_id": "H1", "name": "H", "geo": {"lat": 18.52, "lng": 73.85}, "tier": "tier1"
            },
            "banks": [],
            "units": [],
            "donors": [
                {"donor_id": "ELIGIBLE", "blood_group": "A+", "geo": {"lat": 18.52, "lng": 73.85}, "consent_scopes": ["contactable"]},
                {"donor_id": "INELIGIBLE", "blood_group": "A+", "geo": {"lat": 18.52, "lng": 73.85}, "consent_scopes": ["contactable"]},
                {"donor_id": "WRONG-GROUP", "blood_group": "B+", "geo": {"lat": 18.52, "lng": 73.85}, "consent_scopes": ["contactable"]},
            ],
            "eligible_donor_ids": ["ELIGIBLE", "INELIGIBLE"],
        },
    )

    assert response.status_code == 200
    assert scored_ids == ["ELIGIBLE", "INELIGIBLE"]
