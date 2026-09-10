from types import SimpleNamespace

from contracts.auth import resolve_bank_id_from_organization
from contracts.models import OrganizationType


def test_resolve_bank_id_from_blood_bank_organization_uses_org_id_when_metadata_is_placeholder_or_empty():
    org = SimpleNamespace(
        id="org-123",
        type=OrganizationType.BLOOD_BANK,
        metadata={"bank_id": ""},
    )

    assert resolve_bank_id_from_organization(org) == "org-123"
