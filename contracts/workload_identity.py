"""
Workload Identity integration for service-to-service authentication in GCP.

Enables BloodNet Cloud Run services to authenticate to each other using
Google Cloud's native Workload Identity model without shared secrets.

Spec reference: §4 (identity resolution), §5 (service-to-service trust)
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Any

from fastapi import Header, HTTPException, status


class WorkloadIdentityConfig:
    """Configuration for trusted service accounts and workload identity."""

    def __init__(self):
        self.trusted_accounts = self._parse_trusted_accounts()
        self.service_audience = os.getenv("BLOODNET_SERVICE_AUDIENCE") or "bloodnet-api"
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID")
        self.compute_credentials = None

    def _parse_trusted_accounts(self) -> set[str]:
        """Parse comma-separated list of trusted service accounts from environment."""
        raw = os.getenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS", "")
        return {item.strip() for item in raw.split(",") if item.strip()}

    def is_trusted_account(self, service_account: str) -> bool:
        """Check if a service account is in the trusted list."""
        if not self.trusted_accounts:
            return False
        account = service_account
        if account.startswith("serviceAccount:"):
            account = account.split(":", 1)[1]
        return account in self.trusted_accounts

    def validate_service_claims(self, claims: dict[str, Any]) -> bool:
        """
        Validate incoming service-to-service identity claims.

        Expected claims:
        - sub: service account (with or without 'serviceAccount:' prefix)
        - aud: audience claim (should match BLOODNET_SERVICE_AUDIENCE)
        - azp: authorized party (should match sub)
        - iat/exp: timestamp claims
        """
        if not isinstance(claims, dict):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service identity is missing required claims"
            )

        sub = claims.get("sub")
        aud = claims.get("aud")
        azp = claims.get("azp")

        if not sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service identity is missing subject claim"
            )

        google_identity = claims.get("iss") in {"accounts.google.com", "https://accounts.google.com"}
        account = claims.get("email") if google_identity and claims.get("email_verified") is True else sub
        if not self.is_trusted_account(account):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service identity is not trusted"
            )

        if isinstance(aud, list):
            aud_values = aud
        elif aud:
            aud_values = [aud]
        else:
            aud_values = []

        if self.service_audience not in aud_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Service identity audience mismatch (expected {self.service_audience})"
            )

        if azp and azp != sub:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service identity authorized party does not match subject"
            )

        exp = claims.get("exp")
        if exp and isinstance(exp, (int, float)):
            if exp < datetime.now(timezone.utc).timestamp():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Service identity token has expired"
                )

        return True


_workload_identity_config: WorkloadIdentityConfig | None = None


def get_workload_identity_config() -> WorkloadIdentityConfig:
    """Get or initialize the global workload identity configuration."""
    global _workload_identity_config
    if _workload_identity_config is None:
        _workload_identity_config = WorkloadIdentityConfig()
    return _workload_identity_config


def get_service_identity_from_header(authorization: str) -> dict[str, Any] | None:
    """
    Extract and return identity claims from a service authentication header.

    Expected header format: "Bearer <jwt-token>" or "Workload <jwt-token>"
    """
    if not authorization:
        return None

    parts = authorization.split()
    if len(parts) != 2:
        return None

    scheme, token = parts
    if scheme not in {"Bearer", "Workload", "Credential"}:
        return None

    try:
        if os.getenv("BLOODNET_ENV", "local").lower() in {"production", "prod"}:
            from google.auth.transport.requests import Request
            from google.oauth2.id_token import verify_oauth2_token
            return verify_oauth2_token(token, Request(), audience=get_workload_identity_config().service_audience)
        from contracts.auth_utils import verify_jwt_token
        payload = verify_jwt_token(token)
        if payload:
            return payload
    except Exception:
        pass

    return None


def validate_incoming_service_call(authorization: str) -> dict[str, Any]:
    """
    Validate that an incoming request is from a trusted service.

    Raises HTTPException if validation fails.
    Returns the validated claims dict on success.
    """
    config = get_workload_identity_config()

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service authentication required"
        )

    claims = get_service_identity_from_header(authorization)
    if not claims:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service identity token"
        )

    config.validate_service_claims(claims)
    return claims


def require_service_identity(*allowed_services: str) -> Any:
    """
    FastAPI dependency to require a validated service identity.

    If allowed_services are specified, restricts to those service accounts only.
    """
    def _require_service(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        claims = validate_incoming_service_call(authorization or "")
        sub = claims.get("sub", "")

        if allowed_services:
            service_account = claims.get("email", sub) if claims.get("iss") in {"accounts.google.com", "https://accounts.google.com"} else sub
            if service_account.startswith("serviceAccount:"):
                service_account = service_account.split(":", 1)[1]

            if service_account not in allowed_services and sub not in allowed_services:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Service {service_account} is not allowed to call this endpoint"
                )

        return claims

    return _require_service
