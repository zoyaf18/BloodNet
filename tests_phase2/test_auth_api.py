"""End-to-end API checks for registration, login, and canonical profile behavior."""

from datetime import datetime, timezone
import os
from uuid import UUID, uuid4

import jwt
import pytest

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from contracts.auth import get_identity
from contracts.auth_utils import hash_password
from contracts.models import (
    AuditEventType,
    Identity,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationType,
    RoleType,
    User,
    UserStatus,
)


class FakeAuthRepository:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.user = User(
            id=str(uuid4()), email="person@example.com", display_name="Example Person",
            password_hash=hash_password("SecurePass123!"), email_verified=True,
            mfa_enabled=True, mfa_secret="JBSWY3DPEHPK3PXP",
            status=UserStatus.ACTIVE, created_at=now, updated_at=now,
        )
        self.organization = Organization(
            id=str(uuid4()), name="Example Blood Bank", type=OrganizationType.BLOOD_BANK,
            contact_email="bank@example.com", verified=True, status="active",
            created_at=now, updated_at=now,
        )
        self.membership = OrganizationMembership(
            id=str(uuid4()), user_id=self.user.id, organization_id=self.organization.id,
            role=RoleType.BANK_ADMIN, status=MembershipStatus.ACTIVE,
            created_at=now, updated_at=now,
        )
        self.verification_tokens = []
        self.audit_events = []

    def get_user_by_email(self, email):
        return self.user if self.user and email.lower() == self.user.email else None

    def get_user_by_id(self, user_id):
        return self.user if str(user_id) == self.user.id else None

    def get_user_by_identity_subject(self, subject):
        return self.user if str(subject) == self.user.id else None

    def get_primary_membership(self, user_id):
        return self.membership if str(user_id) == self.user.id else None

    def get_organization_by_id(self, organization_id):
        return self.organization if str(organization_id) == self.organization.id else None

    def create_organization(self, **kwargs):
        metadata = dict(kwargs.get("metadata") or {})
        self.organization = Organization(
            id=str(uuid4()),
            name=kwargs["name"],
            type=kwargs["org_type"],
            contact_email=kwargs["contact_email"],
            contact_phone=kwargs.get("contact_phone"),
            address=kwargs.get("address"),
            metadata=metadata,
            verified=True,
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return self.organization

    def update_organization(self, organization_id, **kwargs):
        if self.organization and str(self.organization.id) == str(organization_id):
            if "status" in kwargs:
                self.organization.status = kwargs["status"]
            if "name" in kwargs:
                self.organization.name = kwargs["name"]
            if "address" in kwargs:
                self.organization.address = kwargs["address"]
            if "contact_email" in kwargs:
                self.organization.contact_email = kwargs["contact_email"]
            if "contact_phone" in kwargs:
                self.organization.contact_phone = kwargs["contact_phone"]
            if "metadata" in kwargs:
                self.organization.metadata = kwargs["metadata"]
        return self.organization

    def verify_organization(self, organization_id, verified_by):
        if self.organization and str(self.organization.id) == str(organization_id):
            self.organization.status = "active"
            self.organization.verified = True
        return self.organization

    def create_membership(self, **kwargs):
        self.membership = OrganizationMembership(
            id=str(uuid4()),
            user_id=str(kwargs["user_id"]),
            organization_id=str(kwargs["organization_id"]),
            role=kwargs["role"],
            status=kwargs.get("status", MembershipStatus.ACTIVE),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        return self.membership

    def update_user(self, user_id, **kwargs):
        if self.user and str(self.user.id) == str(user_id):
            if "email_verified" in kwargs:
                self.user.email_verified = kwargs["email_verified"]
            if "status" in kwargs:
                self.user.status = kwargs["status"]
            if "password_hash" in kwargs:
                self.user.password_hash = kwargs["password_hash"]
        return self.user

    def create_email_verification_token(self, *args, **kwargs):
        self.verification_tokens.append((args, kwargs))

    def log_audit_event(self, *, event_type, resource_type, resource_id, actor_id=None, changes=None, status="success", ip_address=None, user_agent=None):
        self.audit_events.append({
            "event_type": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "actor_id": actor_id,
            "changes": changes,
            "status": status,
            "ip_address": ip_address,
            "user_agent": user_agent,
        })


def build_client(repository):
    import importlib
    os.environ.setdefault("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    auth_api = importlib.import_module("auth_api")
    auth_api.auth_repo = repository
    app = FastAPI()
    app.include_router(auth_api.router)
    app.include_router(auth_api.organization_router)
    return TestClient(app), app, auth_api


def test_regional_preference_cannot_expand_authorization():
    repository = FakeAuthRepository()
    client, app, _ = build_client(repository)
    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id, email=repository.user.email,
        role=RoleType.REGIONAL_ADMIN, region_id="Pune",
    )
    response = client.put("/api/v1/auth/region-preference", json={"region_id": "Mumbai"})
    assert response.status_code == 403


def test_bank_profile_cannot_change_inventory_scope():
    repository = FakeAuthRepository()
    client, app, _ = build_client(repository)
    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id, email=repository.user.email,
        role=RoleType.BANK_ADMIN, organization_id=repository.organization.id,
        bank_id=repository.organization.id, permissions=["organizations.write"],
    )
    response = client.patch("/api/v1/organizations/me", json={"metadata": {"bank_id": "OTHER-BANK"}})
    assert response.status_code == 403
    assert not repository.organization.metadata.get("bank_id")


def test_role_approval_requires_a_regional_administrator():
    _, _, auth_api = build_client(FakeAuthRepository())

    auth_api._require_role_approver(Identity(
        subject_id="regional-user", email="regional@example.com",
        role=RoleType.REGIONAL_ADMIN,
    ))

    with pytest.raises(HTTPException, match="regional administrator"):
        auth_api._require_role_approver(Identity(
            subject_id="donor-user", email="configured@example.com",
            role=RoleType.DONOR,
        ))


def test_role_request_queue_handles_uuid_values_from_postgres():
    repository = FakeAuthRepository()
    repository.list_role_access_requests = lambda: [{
        "id": uuid4(),
        "user_id": repository.user.id,
        "organization_id": repository.organization.id,
        "requested_role": RoleType.BANK_ADMIN.value,
        "status": "pending",
    }]
    client, app, _ = build_client(repository)
    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id,
        email="regional@example.com",
        role=RoleType.REGIONAL_ADMIN,
    )

    response = client.get("/api/v1/auth/role-requests")

    assert response.status_code == 200, response.text
    assert response.json()[0]["user"]["email"] == repository.user.email
    assert response.json()[0]["organization"]["id"] == repository.organization.id


def test_role_request_decision_handles_uuid_values_from_postgres():
    repository = FakeAuthRepository()
    repository.membership.status = MembershipStatus.PENDING_APPROVAL
    repository.organization.status = "pending"
    repository.organization.verified = False
    request_id = uuid4()
    repository.get_role_access_request = lambda value: {
        "id": request_id,
        "user_id": UUID(repository.user.id),
        "organization_id": UUID(repository.organization.id),
        "status": "pending",
    } if str(value) == str(request_id) else None
    repository.get_membership_by_user_org = lambda user_id, organization_id: repository.membership
    repository.update_membership_status = lambda membership_id, status, actor_id: setattr(repository.membership, "status", status)
    repository.update_role_access_request = lambda *args: None
    client, app, _ = build_client(repository)
    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id,
        email="regional@example.com",
        role=RoleType.REGIONAL_ADMIN,
    )

    response = client.post(f"/api/v1/auth/role-requests/{request_id}/approve")

    assert response.status_code == 200, response.text
    assert repository.membership.status == MembershipStatus.ACTIVE
    assert repository.organization.status == "active"
    assert repository.organization.verified is True


def test_membership_role_assignment_verifies_pending_organization():
    repository = FakeAuthRepository()
    repository.organization.status = "pending"
    repository.organization.verified = False
    repository.list_memberships = lambda organization_id: [repository.membership]
    repository.update_membership_role = lambda membership_id, role, updated_by: setattr(repository.membership, "role", role)
    repository.get_organization_by_id = lambda organization_id: repository.organization
    client, app, _ = build_client(repository)
    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id,
        email="regional@example.com",
        role=RoleType.REGIONAL_ADMIN,
    )

    response = client.post(
        f"/api/v1/organizations/{repository.organization.id}/members/{repository.membership.id}/role?role=bank_admin"
    )

    assert response.status_code == 200, response.text
    assert repository.organization.status == "active"
    assert repository.organization.verified is True


def test_identity_repairs_a_previously_approved_pending_organization(monkeypatch):
    """Accounts approved before organization activation was fixed can sign in."""
    import contracts.auth as auth

    repository = FakeAuthRepository()
    repository.organization.status = "pending"
    repository.organization.verified = False
    repository.membership.approved_by = repository.user.id
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setattr(auth, "AuthRepository", lambda _url: repository)

    identity = auth._identity_from_jwt({
        "sub": repository.user.id,
        "email": repository.user.email,
        "mfa_verified": True,
    })

    assert identity.subject_id == repository.user.id
    assert repository.organization.status == "active"
    assert repository.organization.verified is True


def test_identity_derives_hospital_scope_from_hospital_organization(monkeypatch):
    import contracts.auth as auth

    repository = FakeAuthRepository()
    repository.organization = Organization(
        id=str(uuid4()),
        name="Example Hospital",
        type=OrganizationType.HOSPITAL,
        contact_email="hospital@example.com",
        verified=True,
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    repository.membership = OrganizationMembership(
        id=str(uuid4()),
        user_id=repository.user.id,
        organization_id=repository.organization.id,
        role=RoleType.HOSPITAL_COORDINATOR,
        status=MembershipStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setattr(auth, "AuthRepository", lambda _url: repository)

    identity = auth._identity_from_jwt({
        "sub": repository.user.id,
        "email": repository.user.email,
        "mfa_verified": True,
    })

    assert identity.role == RoleType.HOSPITAL_COORDINATOR
    assert identity.hospital_id == repository.organization.id


def test_signup_does_not_accept_client_role(monkeypatch):
    repository = FakeAuthRepository()
    repository.user = None
    client, _, auth_api = build_client(repository)
    created = []

    def create_user(**kwargs):
        created.append(kwargs)
        return User(
            id=str(uuid4()), email=kwargs["email"], display_name=kwargs["display_name"],
            password_hash=kwargs["password_hash"], status=UserStatus.EMAIL_UNVERIFIED,
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        )

    repository.create_user = create_user
    response = client.post("/api/v1/auth/signup", json={
        "email": "new@example.com", "password": "SecurePass123!",
        "password_confirm": "SecurePass123!", "display_name": "New User",
        "role": "bank_admin",
    })
    assert response.status_code == 400
    assert not created


def test_organization_patch_updates_current_organization_profile(monkeypatch):
    repository = FakeAuthRepository()
    repository.organization.metadata = {"region_id": "north-zone", "city": "north-zone"}
    client, app, _ = build_client(repository)
    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id,
        email=repository.user.email,
        role=RoleType.BANK_ADMIN,
        organization_id=repository.organization.id,
        permissions=["organizations.write"],
        bank_id=repository.organization.id,
    )

    response = client.patch(
        "/api/v1/organizations/me",
        json={
            "name": "Updated Blood Bank",
            "contact_email": "updated@example.com",
            "contact_phone": "+1 555 0101",
            "address": "123 Main St",
            "region_id": "north-zone",
            "registration_id": "REG-42",
            "metadata": {"city": "north-zone", "custom": "value"},
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["name"] == "Updated Blood Bank"
    assert payload["contact_email"] == "updated@example.com"
    assert payload["contact_phone"] == "+1 555 0101"
    assert payload["address"] == "123 Main St"
    assert payload["metadata"]["region_id"] == "north-zone"
    assert payload["metadata"]["registration_id"] == "REG-42"
    assert repository.organization.name == "Updated Blood Bank"
    assert repository.organization.contact_email == "updated@example.com"
    assert repository.organization.contact_phone == "+1 555 0101"
    assert repository.organization.metadata["city"] == "north-zone"
    assert repository.organization.metadata["custom"] == "value"


def test_login_and_me_return_server_derived_profile(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "development")
    repository = FakeAuthRepository()
    client, app, auth_api = build_client(repository)
    login = client.post("/api/v1/auth/login", json={"email": repository.user.email, "password": "SecurePass123!"})
    assert login.status_code == 200
    assert login.json()["user"]["role"] == "bank_admin"
    assert login.json()["user"]["subject_id"] == repository.user.id

    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id=repository.user.id, email=repository.user.email, role=RoleType.BANK_ADMIN,
        organization_id=repository.organization.id, permissions=["inventory.read"], bank_id=repository.organization.id,
    )
    profile = client.get("/api/v1/auth/me")
    assert profile.status_code == 200
    assert profile.json()["subject_id"] == repository.user.id
    assert profile.json()["organization"]["id"] == repository.organization.id


def test_me_accepts_demo_identity_subjects_without_uuid(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "development")
    repository = FakeAuthRepository()
    client, app, _ = build_client(repository)

    app.dependency_overrides[get_identity] = lambda: Identity(
        subject_id="local-bank-admin-001",
        email="admin@bloodnet.local",
        role=RoleType.BANK_ADMIN,
        organization_id=repository.organization.id,
        permissions=["inventory.read"],
        bank_id="BANK-001",
    )

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["subject_id"] == "local-bank-admin-001"


def test_login_records_success_audit_event(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "development")
    repository = FakeAuthRepository()
    client, _, _ = build_client(repository)

    response = client.post("/api/v1/auth/login", json={"email": repository.user.email, "password": "SecurePass123!"})

    assert response.status_code == 200
    assert any(event["event_type"] == AuditEventType.LOGIN_SUCCESS.value for event in repository.audit_events)


def test_login_auto_creates_default_membership_when_missing(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "development")
    repository = FakeAuthRepository()
    repository.membership = None
    repository.organization = None
    client, _, _ = build_client(repository)

    response = client.post("/api/v1/auth/login", json={"email": repository.user.email, "password": "SecurePass123!"})

    assert response.status_code == 200, response.text
    payload = jwt.decode(response.json()["access_token"], "dev-secret-change-in-production-32b", algorithms=["HS256"])
    assert payload.get("bank_id") is None
    assert payload["role"] == "donor"
    assert response.json()["user"]["organization"]["name"].endswith("'s Organization")
    assert response.json()["user"]["role"] == "donor"


def test_production_local_login_is_disabled(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "identity-platform")
    repository = FakeAuthRepository()
    client, _, _ = build_client(repository)
    response = client.post("/api/v1/auth/login", json={"email": repository.user.email, "password": "SecurePass123!"})
    assert response.status_code == 410


def test_iap_identity_headers_are_accepted_for_production_auth(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("BLOODNET_AUTH_MODE", "identity-platform")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE", "project-id")

    repository = FakeAuthRepository()
    monkeypatch.setattr("contracts.auth.AuthRepository", lambda *args, **kwargs: repository)
    monkeypatch.setattr(
        "contracts.auth._verify_external_token",
        lambda token: {"sub": repository.user.id, "email": repository.user.email, "mfa_verified": True},
    )
    monkeypatch.setattr(
        "contracts.auth._verify_iap_identity_assertion",
        lambda assertion, request: {"sub": repository.user.id, "email": repository.user.email, "mfa_verified": True},
    )

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/match-svc/api/v1/me",
        "headers": [
            (b"x-goog-iap-jwt-assertion", b"stub-token"),
            (b"x-goog-authenticated-user-email", repository.user.email.encode()),
        ],
    })

    identity = get_identity(request)
    assert identity.email == repository.user.email
    assert identity.organization_id == repository.organization.id
    assert identity.role == RoleType.BANK_ADMIN


def test_bank_admin_uses_org_bank_id_for_scope(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("BLOODNET_AUTH_MODE", "identity-platform")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE", "project-id")

    repository = FakeAuthRepository()
    repository.organization.metadata = {"bank_id": "BANK-001"}
    monkeypatch.setattr("contracts.auth.AuthRepository", lambda *args, **kwargs: repository)
    monkeypatch.setattr(
        "contracts.auth._verify_external_token",
        lambda token: {"sub": repository.user.id, "email": repository.user.email, "mfa_verified": True},
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/match-svc/api/v1/cases/CASE-1",
        "headers": [(b"authorization", f"Bearer stub-token".encode())],
    })

    identity = get_identity(request)
    assert identity.bank_id == "BANK-001"
    assert identity.organization_id == repository.organization.id

    case = type("CaseStub", (), {"inventory_matches": [type("Match", (), {"bank_id": "BANK-001"})()]})()
    from contracts.auth import case_is_in_scope
    assert case_is_in_scope(identity, case, {})


def test_bank_admin_falls_back_to_canonical_bank_id_when_metadata_missing(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("BLOODNET_AUTH_MODE", "identity-platform")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE", "project-id")

    repository = FakeAuthRepository()
    repository.organization.metadata = {}
    monkeypatch.setattr("contracts.auth.AuthRepository", lambda *args, **kwargs: repository)
    monkeypatch.setattr(
        "contracts.auth._verify_external_token",
        lambda token: {"sub": repository.user.id, "email": repository.user.email, "mfa_verified": True},
    )

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/match-svc/api/v1/cases/CASE-1",
        "headers": [(b"authorization", b"Bearer stub-token")],
    })

    identity = get_identity(request)
    assert identity.bank_id == repository.organization.id
    assert identity.organization_id == repository.organization.id


def test_login_rate_limits_failed_attempts(monkeypatch):
    monkeypatch.setenv("BLOODNET_AUTH_MODE", "development")
    repository = FakeAuthRepository()
    client, _, auth_api = build_client(repository)
    auth_api._AUTH_RATE_LIMITS.clear()

    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"email": repository.user.email, "password": "WrongPass123!"})
        assert response.status_code == 401

    throttled = client.post("/api/v1/auth/login", json={"email": repository.user.email, "password": "WrongPass123!"})
    assert throttled.status_code == 429
    assert "Too many requests" in throttled.json()["detail"]


def test_totp_code_is_valid_for_mfa_secret():
    import pyotp
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert pyotp.TOTP(secret).verify(code)


def test_synthetic_donor_subject_is_auto_registered(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("BLOODNET_AUTH_MODE", "identity-platform")
    monkeypatch.setenv("BLOODNET_DATABASE_URL", "postgresql://test:test@localhost/test")
    monkeypatch.setenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE", "project-id")

    class Repo:
        def __init__(self, *args, **kwargs):
            self.created = []

        def get_user_by_identity_subject(self, subject):
            return None

        def get_user_by_id(self, user_id):
            return None

        def create_user(self, **kwargs):
            self.created.append(kwargs)
            user = User(
                id=str(uuid4()), email=kwargs["email"], display_name=kwargs.get("display_name"),
                identity_subject=kwargs.get("identity_subject"), status=UserStatus.ACTIVE,
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )
            return user

        def get_organization_by_type(self, org_type):
            return None

        def create_organization(self, **kwargs):
            return Organization(
                id=str(uuid4()), name=kwargs["name"], type=kwargs["org_type"],
                contact_email=kwargs["contact_email"], status="active",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )

        def verify_organization(self, org_id, user_id):
            return Organization(
                id=str(org_id), name="BloodNet Platform", type=OrganizationType.PLATFORM,
                contact_email="admin@bloodnet.local", status="active",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )

        def create_membership(self, **kwargs):
            self.created.append({"membership": kwargs})
            return OrganizationMembership(
                id=str(uuid4()), user_id=str(kwargs["user_id"]), organization_id=str(kwargs["organization_id"]),
                role=kwargs["role"], status=MembershipStatus.ACTIVE,
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )

        def get_primary_membership(self, user_id):
            return None

        def get_organization_by_id(self, organization_id):
            return Organization(
                id=str(organization_id), name="BloodNet Platform", type=OrganizationType.PLATFORM,
                contact_email="admin@bloodnet.local", status="active",
                created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
            )

    repo = Repo()
    monkeypatch.setattr("contracts.auth.AuthRepository", lambda *args, **kwargs: repo)
    monkeypatch.setattr(
        "contracts.auth.verify_jwt_token",
        lambda token: {"sub": "DONOR-001", "email": "donor@example.com", "role": "donor", "mfa_verified": True},
    )

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/swarm-svc/api/v1/opportunities",
        "headers": [(b"authorization", b"Bearer stub-token")],
    })

    identity = get_identity(request)
    assert identity.role == RoleType.DONOR
    assert identity.email == "donor@example.com"
    assert repo.created
