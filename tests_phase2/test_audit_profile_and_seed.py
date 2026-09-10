"""Source-fix regressions; these tests never connect to production."""
from datetime import datetime, timedelta, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from contracts.auth import Identity


@pytest.mark.parametrize("years_from_today", [1, -10, -17])
def test_invalid_donor_age_is_rejected_before_any_write(monkeypatch, years_from_today):
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://unused:unused@127.0.0.1:1/unused")
    import auth_api
    repository = Mock()
    monkeypatch.setattr(auth_api, "auth_repo", repository)
    app = FastAPI()
    app.include_router(auth_api.router)
    app.dependency_overrides[auth_api.get_identity] = lambda: Identity("audit-donor", "donor")
    dob = datetime.now(timezone.utc).date() + timedelta(days=365 * years_from_today)
    with TestClient(app) as client:
        response = client.put("/api/v1/auth/donor-profile", json={
            "display_name": "Audit Donor", "blood_group": "AB-", "date_of_birth": dob.isoformat(),
            "city": "Pune", "region_id": "Pune", "notification_channels": ["email"],
        })
    assert response.status_code == 400
    assert "18 years" in response.json()["detail"]
    assert repository.mock_calls == []


def test_synthetic_demand_totals_balance_even_for_one_unit_requests():
    path = Path(__file__).resolve().parents[1] / "scripts/seed_synthetic_india.py"
    spec = spec_from_file_location("audit_seed", path)
    seed = module_from_spec(spec)
    spec.loader.exec_module(seed)
    _, organizations = seed.build_organizations(100)
    _, _, _, donors = seed.build_users(100, organizations)
    *_, demand = seed.build_domain_rows(1000, organizations, donors)
    assert any(row[5] == 1 for row in demand)
    for *_, requested, fulfilled, shortage in demand:
        assert requested == fulfilled + shortage
        assert 0 <= shortage <= requested
