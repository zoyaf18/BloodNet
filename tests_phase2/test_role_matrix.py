import pytest
from fastapi import HTTPException

from contracts.auth import Identity, require_role, require_resource_scope
from contracts.auth_permissions import permissions_for_role
from contracts.models import RoleType


@pytest.mark.parametrize(
    ("role", "expected_perms"),
    [
        (RoleType.DONOR, {"cases.read", "donations.read", "donations.respond"}),
        (RoleType.HOSPITAL_COORDINATOR, {"cases.read", "cases.create", "cases.approve", "inventory.read"}),
        (RoleType.BANK_ADMIN, {"cases.read", "inventory.read", "inventory.reserve", "inventory.release", "recommendations.read", "recommendations.approve"}),
        (RoleType.REGIONAL_ADMIN, {"cases.read", "cases.create", "inventory.read", "organizations.read", "organizations.verify", "recommendations.read", "recommendations.approve"}),
        (RoleType.AUDITOR, {"cases.read", "organizations.read", "audit.read", "recommendations.read", "recommendations.approve"}),
    ],
)
def test_role_permission_matrix(role, expected_perms):
    actual = set(permissions_for_role(role))
    assert actual == expected_perms


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (RoleType.DONOR, False),
        (RoleType.HOSPITAL_COORDINATOR, False),
        (RoleType.BANK_ADMIN, False),
        (RoleType.REGIONAL_ADMIN, True),
        (RoleType.AUDITOR, True),
    ],
)
def test_audit_access_is_only_for_audit_and_regional_roles(role, allowed):
    identity = Identity(
        subject_id=f"{role.value}-user",
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
        organization_id="ORG-1",
        permissions=permissions_for_role(role),
        bank_id="BANK-001" if role == RoleType.BANK_ADMIN else None,
        hospital_id="HOSP-001" if role == RoleType.HOSPITAL_COORDINATOR else None,
        region_id="REG-01" if role in {RoleType.REGIONAL_ADMIN, RoleType.AUDITOR} else None,
    )
    if allowed:
        require_role(identity, "auditor", "regional_admin")
    else:
        with pytest.raises(HTTPException):
            require_role(identity, "auditor", "regional_admin")


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        (RoleType.DONOR, True),
        (RoleType.HOSPITAL_COORDINATOR, False),
        (RoleType.BANK_ADMIN, False),
        (RoleType.REGIONAL_ADMIN, False),
        (RoleType.AUDITOR, False),
    ],
)
def test_donor_opportunity_access_is_limited_to_donors(role, allowed):
    identity = Identity(
        subject_id=f"{role.value}-user",
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
        organization_id="ORG-1",
        permissions=permissions_for_role(role),
        bank_id=None,
        hospital_id=None,
        region_id=None,
    )
    if allowed:
        require_role(identity, "donor")
    else:
        with pytest.raises(HTTPException):
            require_role(identity, "donor")


@pytest.mark.parametrize(
    ("role", "scope_type", "resource_id", "expected"),
    [
        (RoleType.HOSPITAL_COORDINATOR, "hospital", "HOSP-001", True),
        (RoleType.HOSPITAL_COORDINATOR, "hospital", "HOSP-002", False),
        (RoleType.BANK_ADMIN, "bank", "BANK-001", True),
        (RoleType.BANK_ADMIN, "bank", "BANK-002", False),
        (RoleType.REGIONAL_ADMIN, "region", "REG-01", True),
        (RoleType.REGIONAL_ADMIN, "region", "REG-02", False),
        (RoleType.AUDITOR, "region", "REG-01", True),
    ],
)
def test_resource_scope_matrix(role, scope_type, resource_id, expected):
    identity = Identity(
        subject_id=f"{role.value}-user",
        email=f"{role.value}@example.com",
        display_name=role.value,
        role=role,
        organization_id="ORG-1",
        permissions=permissions_for_role(role),
        bank_id="BANK-001" if role == RoleType.BANK_ADMIN else None,
        hospital_id="HOSP-001" if role == RoleType.HOSPITAL_COORDINATOR else None,
        region_id="REG-01" if role in {RoleType.REGIONAL_ADMIN, RoleType.AUDITOR} else None,
    )
    if expected:
        require_resource_scope(identity, resource_id, scope_type=scope_type, label=scope_type)
    else:
        with pytest.raises(HTTPException):
            require_resource_scope(identity, resource_id, scope_type=scope_type, label=scope_type)
