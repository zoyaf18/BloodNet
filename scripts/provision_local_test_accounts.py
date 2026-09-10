"""Create disposable local accounts for browser role verification."""

import json
import os
import secrets
from pathlib import Path

from contracts.auth_repository import AuthRepository
from contracts.auth_utils import hash_password
from contracts.models import OrganizationType, RoleType, UserStatus


ROLES = {
    "donor": ("donor", "Donor Test", OrganizationType.PLATFORM, {}),
    "donor2": ("donor2", "Donor Test 2", OrganizationType.PLATFORM, {}),
    "hospital_coordinator": ("hospital", "Hospital Test", OrganizationType.HOSPITAL, {"hospital_id": "HOSP-001"}),
    "bank_admin": ("bank", "Bank Test", OrganizationType.BLOOD_BANK, {"bank_id": "BANK-001"}),
    "regional_admin": ("regional", "Regional Test", OrganizationType.REGIONAL, {"region_id": "Pune"}),
    "auditor": ("auditor", "Auditor Test", OrganizationType.PLATFORM, {}),
}


def main() -> None:
    database_url = os.environ.get("BLOODNET_DATABASE_URL", "postgresql://bloodnet:bloodnet_local@localhost:5432/bloodnet")
    proxy_port = os.environ.get("BLOODNET_CLOUD_SQL_PROXY_PORT")
    if proxy_port and "?" in database_url:
        database_url = database_url.split("?", 1)[0] + f"?host=127.0.0.1&port={proxy_port}"
    repository = AuthRepository(database_url)
    fixed_credentials_path = os.environ.get("BLOODNET_FIXED_CREDENTIALS_PATH")
    fixed_credentials = {}
    if fixed_credentials_path:
        fixed_credentials = json.loads(Path(fixed_credentials_path).read_text(encoding="utf-8"))
    credentials = {}
    for role_name, (slug, display_name, organization_type, metadata) in ROLES.items():
        email = f"{slug}@bloodnet.local"
        password = fixed_credentials.get(role_name, {}).get("password") or f"BN-local-{secrets.token_urlsafe(16)}!"
        existing = repository.get_user_by_email(email)
        if existing:
            user = existing
            user = repository.update_user(user.id, password_hash=hash_password(password), status=UserStatus.ACTIVE, email_verified=True)
        else:
            user = repository.create_user(email=email, display_name=display_name, password_hash=hash_password(password))
            user = repository.update_user(user.id, status=UserStatus.ACTIVE, email_verified=True)
        organization = repository.get_organization_by_type(organization_type)
        if not organization:
            organization = repository.create_organization(name=f"{display_name} Organization", org_type=organization_type, contact_email=email, metadata=metadata)
            organization = repository.verify_organization(organization.id, user.id)
        membership = repository.get_membership_by_user_org(user.id, organization.id)
        if not membership:
            membership_role = RoleType.DONOR if role_name == "donor2" else RoleType(role_name)
            repository.create_membership(user_id=user.id, organization_id=organization.id, role=membership_role)
        credentials[role_name] = {"email": email, "password": password}
    # Browser checks use disposable credentials.  Let callers choose a separate
    # file so an existing developer credential file is never overwritten.
    output = Path(os.environ.get("BLOODNET_TEST_CREDENTIALS_PATH", Path(__file__).resolve().parents[1] / ".local-test-credentials.json"))
    output.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf-8")
    print(f"Provisioned {len(credentials)} local role accounts: {output}")


if __name__ == "__main__":
    main()
