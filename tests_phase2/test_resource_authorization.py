import pytest
from fastapi import HTTPException

from contracts.auth import Identity, case_is_in_scope, require_resource_scope, validate_service_identity
from contracts.models import Case, InventoryMatch


def scoped_case():
    case = Case(case_id="CASE-1", request_id="REQ-1")
    case.inventory_matches = [
        InventoryMatch(bank_id="BANK-1", units_available=2, distance_km=1.0, eta_min=5)
    ]
    return case


def test_hospital_only_sees_its_cases():
    case = scoped_case()
    assert case_is_in_scope(Identity("HOSP-USER", "hospital_coordinator", hospital_id="HOSP-1"), case, {"hospital_id": "HOSP-1"})
    assert not case_is_in_scope(Identity("HOSP-USER", "hospital_coordinator", hospital_id="HOSP-2"), case, {"hospital_id": "HOSP-1"})


def test_bank_only_sees_its_inventory_cases():
    case = scoped_case()
    assert case_is_in_scope(Identity("BANK-USER", "bank_admin", bank_id="BANK-1"), case, {})
    assert not case_is_in_scope(Identity("BANK-USER", "bank_admin", bank_id="BANK-2"), case, {})


def test_regional_and_auditor_roles_are_explicitly_scoped_by_role():
    case = scoped_case()
    # Regionless legacy cases must not leak into a regional administrator's
    # view.  Only auditors retain global historical visibility.
    assert not case_is_in_scope(Identity("REGION-USER", "regional_admin", region="Pune"), case, {})
    assert case_is_in_scope(Identity("REGION-USER", "regional_admin", region="Pune"), case, {"region": "Pune"})
    assert not case_is_in_scope(Identity("REGION-USER", "regional_admin", region="Pune"), case, {"region": "Mumbai"})
    assert case_is_in_scope(Identity("AUDIT-USER", "auditor"), case, {})
    assert not case_is_in_scope(Identity("DONOR-1", "donor"), case, {})


def test_require_resource_scope_rejects_cross_organization_access():
    identity = Identity("user-1", "user@example.com", "hospital_coordinator", organization_id="ORG-1", hospital_id="HOSP-1")

    require_resource_scope(identity, "ORG-1", label="organization")
    with pytest.raises(HTTPException):
        require_resource_scope(identity, "ORG-2", label="organization")

    require_resource_scope(identity, "HOSP-1", scope_type="hospital", label="hospital")
    with pytest.raises(HTTPException):
        require_resource_scope(identity, "HOSP-2", scope_type="hospital", label="hospital")


def test_validate_service_identity_requires_trusted_workload_claims(monkeypatch):
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project-id.iam.gserviceaccount.com")

    claims = {
        "sub": "serviceAccount:match-svc@project-id.iam.gserviceaccount.com",
        "aud": "bloodnet-api",
        "azp": "match-svc@project-id.iam.gserviceaccount.com",
    }
    assert validate_service_identity(claims) is True

    with pytest.raises(HTTPException):
        validate_service_identity({"sub": "serviceAccount:other@project-id.iam.gserviceaccount.com", "aud": "bloodnet-api"})
