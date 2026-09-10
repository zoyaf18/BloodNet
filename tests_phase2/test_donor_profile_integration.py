"""Integration coverage for donor identity provisioning and persistent preferences."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from contracts.auth import _identity_from_jwt
from contracts.auth_repository import AuthRepository
from contracts.models import Identity, OrganizationType, RoleType, UserStatus


@pytest.fixture
def donor_identity():
    repository = AuthRepository(__import__("os").environ["BLOODNET_DATABASE_URL"])
    user = repository.create_user(
        email=f"donor-{uuid4().hex}@example.com",
        display_name="Integration Donor",
        phone="+919999999999",
    )
    user = repository.update_user(user.id, status=UserStatus.ACTIVE, email_verified=True)
    organization = repository.create_organization(
        name=f"Donor Test Platform {uuid4().hex}",
        org_type=OrganizationType.PLATFORM,
        contact_email="platform@example.com",
    )
    organization = repository.verify_organization(organization.id, user.id)
    repository.create_membership(
        user_id=user.id,
        organization_id=organization.id,
        role=RoleType.DONOR,
    )
    return repository, Identity(
        subject_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=RoleType.DONOR,
        organization_id=organization.id,
    )


def profile_client(repository, identity):
    import sys
    from pathlib import Path

    match_dir = Path(__file__).resolve().parents[1] / "services" / "match-svc"
    if str(match_dir) not in sys.path:
        sys.path.insert(0, str(match_dir))
    import auth_api

    auth_api.auth_repo = repository
    app = FastAPI()
    app.include_router(auth_api.router)
    app.dependency_overrides[auth_api.get_identity] = lambda: identity
    return TestClient(app)


def test_donor_profile_preferences_survive_api_round_trip(donor_identity):
    repository, identity = donor_identity
    client = profile_client(repository, identity)
    payload = {
        "display_name": "Updated Donor",
        "blood_group": "O+",
        "date_of_birth": "1992-04-10",
        "city": "Pune",
        "region_id": "Pune",
        "availability": "paused",
        "consent_contact": True,
        "notification_channels": ["email"],
        "eligibility_status": "deferred",
        "next_eligible_at": "2026-10-01T12:00:00Z",
        "donation_history": [{"date": "2026-08-01", "status": "completed"}],
    }

    saved = client.put("/api/v1/auth/donor-profile", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["notification_channels"] == ["email"]
    assert saved.json()["eligibility_status"] == "eligible"

    reopened = AuthRepository(__import__("os").environ["BLOODNET_DATABASE_URL"])
    profile = reopened.get_donor_profile(identity.subject_id)
    assert profile is not None
    assert profile["consent_contact"] is True
    assert profile["availability"] == "paused"
    assert profile["donation_history"] == []

    loaded = client.get("/api/v1/auth/donor-profile")
    assert loaded.status_code == 200
    assert loaded.json()["city"] == "Pune"
    assert loaded.json()["next_eligible_at"] is not None


def test_donor_profile_rejects_unsupported_preferences(donor_identity):
    repository, identity = donor_identity
    client = profile_client(repository, identity)
    response = client.put(
        "/api/v1/auth/donor-profile",
        json={
            "display_name": "Invalid Donor",
            "date_of_birth": "1999-10-07",
            "city": "Pune", "region_id": "Pune",
            "blood_group": "O+",
            "availability": "available",
            "consent_contact": False,
            "notification_channels": ["whatsapp"],
            "eligibility_status": "eligible",
        },
    )
    assert response.status_code == 400
    assert "notification channel" in response.json()["detail"]


def test_donor_profile_requires_city(donor_identity):
    repository, identity = donor_identity
    client = profile_client(repository, identity)

    response = client.put(
        "/api/v1/auth/donor-profile",
        json={
            "display_name": "Location-less Donor",
            "blood_group": "O+",
            "date_of_birth": "1992-04-10",
            "city": "",
            "region_id": "Pune",
            "availability": "available",
            "consent_contact": False,
            "notification_channels": ["email"],
            "eligibility_status": "eligible",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]


def test_donor_profile_rejects_underage_donor(donor_identity):
    repository, identity = donor_identity
    client = profile_client(repository, identity)

    underage_dob = (datetime.now(timezone.utc).date() - timedelta(days=365 * 17 + 30)).isoformat()

    response = client.put(
        "/api/v1/auth/donor-profile",
        json={
            "display_name": "Minor Donor",
            "blood_group": "O+",
            "date_of_birth": underage_dob,
            "city": "Pune",
        "region_id": "Pune",
            "availability": "available",
            "consent_contact": False,
            "notification_channels": ["email"],
            "eligibility_status": "eligible",
        },
    )

    assert response.status_code == 400
    assert "at least 18" in response.json()["detail"]


@pytest.mark.parametrize("birth_date", [None, "2999-10-07"])
def test_donor_profile_requires_adult_birth_date(donor_identity, birth_date):
    repository, identity = donor_identity
    client = profile_client(repository, identity)
    response = client.put("/api/v1/auth/donor-profile", json={
        "display_name": "Age validation", "blood_group": "O+", "date_of_birth": birth_date,
        "city": "Pune", "region_id": "Pune", "availability": "available",
        "consent_contact": False, "notification_channels": ["email"],
    })
    assert response.status_code == 400
    assert "18" in response.json()["detail"]


def test_donor_profile_accepts_eighteenth_birthday(donor_identity):
    repository, identity = donor_identity
    today = datetime.now(timezone.utc).date()
    birthday = today.replace(year=today.year - 18, day=min(today.day, 28))
    response = profile_client(repository, identity).put("/api/v1/auth/donor-profile", json={
        "display_name": "Adult validation", "blood_group": "O+", "date_of_birth": birthday.isoformat(),
        "city": "Pune", "region_id": "Pune", "availability": "available",
        "consent_contact": False, "notification_channels": ["email"],
    })
    assert response.status_code == 200


def test_phone_subject_is_provisioned_as_donor(monkeypatch):
    database_url = __import__("os").environ["BLOODNET_DATABASE_URL"]
    repository = AuthRepository(database_url)
    monkeypatch.setattr("contracts.auth.AuthRepository", lambda _: repository)
    subject = f"firebase-phone-{uuid4().hex}"

    identity = _identity_from_jwt({
        "sub": subject,
        "phone_number": "+919888888888",
        "name": "Phone Donor",
    })

    assert identity.role == RoleType.DONOR
    user = repository.get_user_by_id(identity.subject_id)
    assert user is not None
    assert user.phone == "+919888888888"
    assert user.email == f"{subject}@phone.identity.invalid"


def test_profile_edit_cannot_remove_recorded_donation_deferral(donor_identity):
    repository, identity = donor_identity
    client = profile_client(repository, identity)
    payload = {"display_name": "Review Donor", "blood_group": "O+", "date_of_birth": "1992-04-10",
               "city": "Pune", "region_id": "Pune", "availability": "available", "consent_contact": True,
               "notification_channels": ["email"]}
    payload["last_donation_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    first = client.put("/api/v1/auth/donor-profile", json=payload)
    assert first.status_code == 200, first.text
    payload["last_donation_at"] = None
    updated = client.put("/api/v1/auth/donor-profile", json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()["next_eligible_at"] == first.json()["next_eligible_at"]
    payload["last_donation_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert client.put("/api/v1/auth/donor-profile", json=payload).status_code == 400
