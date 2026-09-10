"""Regression tests for service-to-service identity and workload identity."""

import json
import os
import pytest
import jwt
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Depends
from fastapi.testclient import TestClient

from contracts.workload_identity import (
    WorkloadIdentityConfig,
    get_service_identity_from_header,
    validate_incoming_service_call,
    require_service_identity,
    get_workload_identity_config,
)
from contracts.auth_utils import generate_jwt_token


def test_workload_identity_config_parses_trusted_accounts(monkeypatch):
    """Test that trusted service accounts are parsed from environment."""
    monkeypatch.setenv(
        "BLOODNET_TRUSTED_SERVICE_ACCOUNTS",
        "match-svc@project.iam.gserviceaccount.com,notify-svc@project.iam.gserviceaccount.com"
    )
    config = WorkloadIdentityConfig()
    assert len(config.trusted_accounts) == 2
    assert "match-svc@project.iam.gserviceaccount.com" in config.trusted_accounts
    assert "notify-svc@project.iam.gserviceaccount.com" in config.trusted_accounts


def test_workload_identity_config_is_trusted_account(monkeypatch):
    """Test account trust validation with and without prefix."""
    monkeypatch.setenv(
        "BLOODNET_TRUSTED_SERVICE_ACCOUNTS",
        "match-svc@project.iam.gserviceaccount.com"
    )
    config = WorkloadIdentityConfig()

    assert config.is_trusted_account("match-svc@project.iam.gserviceaccount.com") is True
    assert config.is_trusted_account("serviceAccount:match-svc@project.iam.gserviceaccount.com") is True
    assert config.is_trusted_account("other-svc@project.iam.gserviceaccount.com") is False


def test_workload_identity_config_validate_service_claims_requires_sub(monkeypatch):
    """Test that missing subject claim is rejected."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com")
    config = WorkloadIdentityConfig()

    with pytest.raises(HTTPException) as exc_info:
        config.validate_service_claims({"aud": "bloodnet-api"})
    assert exc_info.value.status_code == 403
    assert "subject" in exc_info.value.detail.lower()


def test_workload_identity_config_validate_service_claims_requires_trusted_account(monkeypatch):
    """Test that untrusted service accounts are rejected."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com")
    config = WorkloadIdentityConfig()

    with pytest.raises(HTTPException) as exc_info:
        config.validate_service_claims({
            "sub": "untrusted-svc@project.iam.gserviceaccount.com",
            "aud": "bloodnet-api"
        })
    assert exc_info.value.status_code == 403
    assert "not trusted" in exc_info.value.detail.lower()


def test_workload_identity_config_validate_service_claims_checks_audience(monkeypatch):
    """Test that audience mismatch is rejected."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com")
    monkeypatch.setenv("BLOODNET_SERVICE_AUDIENCE", "bloodnet-api")
    config = WorkloadIdentityConfig()

    with pytest.raises(HTTPException) as exc_info:
        config.validate_service_claims({
            "sub": "match-svc@project.iam.gserviceaccount.com",
            "aud": "wrong-audience"
        })
    assert exc_info.value.status_code == 403
    assert "audience" in exc_info.value.detail.lower()


def test_workload_identity_config_validate_service_claims_accepts_list_audience(monkeypatch):
    """Test that audience claim can be a list."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com")
    monkeypatch.setenv("BLOODNET_SERVICE_AUDIENCE", "bloodnet-api")
    config = WorkloadIdentityConfig()

    result = config.validate_service_claims({
        "sub": "match-svc@project.iam.gserviceaccount.com",
        "aud": ["other-audience", "bloodnet-api"]
    })
    assert result is True


def test_workload_identity_config_validate_service_claims_checks_expiry(monkeypatch):
    """Test that expired tokens are rejected."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com")
    config = WorkloadIdentityConfig()

    past_timestamp = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    with pytest.raises(HTTPException) as exc_info:
        config.validate_service_claims({
            "sub": "match-svc@project.iam.gserviceaccount.com",
            "aud": "bloodnet-api",
            "exp": past_timestamp
        })
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_get_service_identity_from_bearer_header(monkeypatch):
    """Test extraction of identity from Bearer token header."""
    monkeypatch.setenv("BLOODNET_JWT_SECRET", "test-secret-key-for-testing")
    
    # Generate a valid JWT token
    token, _ = generate_jwt_token(
        subject_id=str(uuid4()),
        email="service@example.com",
        role="regional_admin",
        permissions=[]
    )
    
    claims = get_service_identity_from_header(f"Bearer {token}")
    assert claims is not None
    assert claims.get("sub") is not None


def test_get_service_identity_from_header_rejects_invalid_scheme():
    """Test that invalid authorization schemes are rejected."""
    result = get_service_identity_from_header("Basic abc123")
    assert result is None


def test_validate_incoming_service_call_end_to_end(monkeypatch):
    """Test full service call validation flow."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com")
    monkeypatch.setenv("BLOODNET_SERVICE_AUDIENCE", "bloodnet-api")
    
    config = get_workload_identity_config()
    
    # Valid service call
    claims = {
        "sub": "match-svc@project.iam.gserviceaccount.com",
        "aud": "bloodnet-api",
        "iat": datetime.now(timezone.utc).timestamp(),
        "exp": (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
    }
    
    result = config.validate_service_claims(claims)
    assert result is True


def test_require_service_identity_dependency_rejects_missing_auth():
    """Test that FastAPI dependency rejects missing authentication."""
    app = FastAPI()

    @app.get("/test")
    def test_endpoint(claims=Depends(require_service_identity())):
        return {"claims": claims}

    client = TestClient(app)
    response = client.get("/test")
    assert response.status_code == 401


def test_require_service_identity_dependency_validates_allowed_services(monkeypatch):
    """Test that FastAPI dependency can restrict to specific services."""
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "match-svc@project.iam.gserviceaccount.com,notify-svc@project.iam.gserviceaccount.com")
    monkeypatch.setenv("BLOODNET_SERVICE_AUDIENCE", "bloodnet-api")
    monkeypatch.setenv("BLOODNET_JWT_SECRET", "dev-secret-change-in-production-32b")
    
    app = FastAPI()

    @app.get("/test")
    def test_endpoint(claims=Depends(require_service_identity("match-svc@project.iam.gserviceaccount.com"))):
        return {"status": "ok"}

    # Generate a valid token for match-svc with the default secret
    token, _ = generate_jwt_token(
        subject_id="match-svc@project.iam.gserviceaccount.com",
        email="match-svc@project.iam.gserviceaccount.com",
        role="regional_admin",
        permissions=[]
    )
    
    client = TestClient(app)
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    # A token without the required service audience must be rejected.
    assert response.status_code in {401, 403}


def test_production_worker_validates_google_token_from_header(monkeypatch):
    import contracts.workload_identity as workload
    account = "worker@project.iam.gserviceaccount.com"
    audience = "https://candidate.a.run.app"
    monkeypatch.setenv("BLOODNET_ENV", "production")
    monkeypatch.setenv("BLOODNET_SERVICE_AUDIENCE", audience)
    monkeypatch.setenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", account)
    monkeypatch.setattr(workload, "_workload_identity_config", None)
    observed = []
    claims = {"iss": "https://accounts.google.com", "sub": "123456", "azp": "123456",
              "email": account, "email_verified": True, "aud": audience}
    def verify(token, request, audience=None):
        observed.append((token, audience))
        return claims
    monkeypatch.setattr("google.oauth2.id_token.verify_oauth2_token", verify)
    app = FastAPI()
    @app.post("/worker")
    def worker(identity=Depends(require_service_identity(account))):
        return {"ok": True}
    client = TestClient(app)
    assert client.post("/worker?authorization=Bearer%20google-token").status_code == 401
    assert client.post("/worker", headers={"Authorization": "Bearer google-token"}).status_code == 200
    assert observed == [("google-token", audience)]
    claims["email"] = "untrusted@project.iam.gserviceaccount.com"
    assert client.post("/worker", headers={"Authorization": "Bearer google-token"}).status_code == 403
