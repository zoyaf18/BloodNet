"""
Authenticated identity and authorization context for service boundaries.

Two-layer auth system per spec §4-5:
1. External JWT verification (identity verification)
2. BloodNet role/permission resolution (authorization)

In development mode (BLOODNET_AUTH_MODE=development), uses a demo identity.
In production, integrates with Identity Platform and BloodNet user database.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status

from contracts.auth_utils import verify_jwt_token
from contracts.auth_repository import AuthRepository
from contracts.auth_permissions import permissions_for_role
from contracts.models import Case, Identity, MembershipStatus, OrganizationType, RoleType, UserStatus

VALID_ROLES = {"donor", "hospital_coordinator", "bank_admin", "regional_admin", "auditor"}


def _as_role_type(value: Any) -> RoleType | None:
    if value is None:
        return None
    try:
        return RoleType(value)
    except ValueError:
        return None


def get_identity_platform_audience() -> str | None:
    """Return the configured Identity Platform audience for Firebase/Google ID token verification."""
    return (
        os.getenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE")
        or os.getenv("GCP_PROJECT_ID")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
    )


def validate_identity_platform_configuration() -> dict[str, Any]:
    """Validate the deployment configuration required for live Identity Platform verification."""
    audience = get_identity_platform_audience()
    if not audience:
        raise RuntimeError(
            "Identity Platform is not configured. Set BLOODNET_IDENTITY_PLATFORM_AUDIENCE or GOOGLE_CLOUD_PROJECT/GCP_PROJECT_ID."
        )
    return {
        "audience": audience,
        "project_id": os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"),
        "configured": True,
    }


def get_identity(request: Request) -> Identity:
    """
    Resolve authenticated identity from the trusted production edge identity.

    In production, the final enforcement plane is IAP + Identity Platform + the
    API gateway. Requests may arrive with either an Authorization bearer token or
    the Google IAP identity headers injected by the edge. The backend must accept
    either path and then resolve the BloodNet org membership/role data from the
    resulting identity claims.
    """
    authorization = request.headers.get("Authorization", "")

    # Preferred production flow: edge (IAP/API Gateway) has already verified the
    # user identity and passes identity metadata in headers to the backend.
    iap_assertion = request.headers.get("x-goog-iap-jwt-assertion")
    if iap_assertion:
        payload = _verify_iap_identity_assertion(iap_assertion, request)
        if payload:
            return _identity_from_jwt(payload)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired IAP identity assertion"
        )

    # Try JWT token from Authorization header
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        payload = verify_jwt_token(token)
        if not payload and os.environ.get("BLOODNET_AUTH_MODE") in {"production", "identity-platform"}:
            payload = _verify_external_token(token)
        if not payload and os.environ.get("BLOODNET_AUTH_MODE") in {"production", "identity-platform"}:
            payload = _verify_gateway_userinfo(request)
        if payload:
            return _identity_from_jwt(payload)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token"
        )

    # Development fallback
    if os.environ.get("BLOODNET_AUTH_MODE") == "development":
        return Identity(
            subject_id="local-bank-admin-001",
            email="admin@bloodnet.local",
            display_name="Local Admin",
            role=RoleType.BANK_ADMIN,
            organization_id="local-org-001",
            permissions=[
                "cases.read", "cases.create",
                "inventory.read", "inventory.reserve", "inventory.release",
                "recommendations.read", "recommendations.approve",
                "users.manage",
                "audit.read"
            ],
            bank_id="BANK-001",
            hospital_id=None,
            region_id=None,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required"
    )


def _identity_from_jwt(payload: dict[str, Any]) -> Identity:
    """
    Construct Identity from verified JWT payload.

    Supports both canonical UUID-backed IDs and synthetic demo identities such as
    DONOR-001, HOSPITAL-001, and similar IDs used in local/demo flows.
    """
    subject_id = str(payload.get("sub") or "").strip()
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if not subject_id or not database_url:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authenticated subject"
        )

    repository = AuthRepository(database_url)
    try:
        user = repository.get_user_by_identity_subject(subject_id)
        if not user and payload.get("email") and hasattr(repository, "get_user_by_email"):
            user = repository.get_user_by_email(payload["email"])
            if user and not user.identity_subject:
                user = repository.update_user(user.id, identity_subject=subject_id)
        if not user:
            try:
                UUID(subject_id)
            except ValueError:
                user = None
            else:
                user = repository.get_user_by_id(subject_id)
        membership = repository.get_primary_membership(user.id) if user else None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to resolve authenticated user"
        )
    if not user:
        email = payload.get("email") or f"{subject_id}@phone.identity.invalid"
        fallback_role = RoleType.DONOR
        user = repository.create_user(
            email=email,
            display_name=payload.get("name") or email,
            phone=payload.get("phone_number"),
            identity_subject=subject_id,
        )
        if hasattr(repository, "update_user"):
            user = repository.update_user(
                user.id,
                status=UserStatus.ACTIVE,
                email_verified=True,
            )
        platform = repository.get_organization_by_type(OrganizationType.PLATFORM)
        if not platform:
            platform = repository.create_organization(
                name="BloodNet Platform",
                org_type=OrganizationType.PLATFORM,
                contact_email="admin@bloodnet.local",
            )
            platform = repository.verify_organization(platform.id, user.id)
        membership = repository.create_membership(
            user_id=user.id,
            organization_id=platform.id,
            role=fallback_role,
        )
        if not membership:
            membership = repository.get_primary_membership(user.id)
    if not membership:
        email = payload.get("email") or f"{subject_id}@phone.identity.invalid"
        fallback_role = RoleType.DONOR
        platform = repository.get_organization_by_type(OrganizationType.PLATFORM)
        if not platform:
            platform = repository.create_organization(
                name="BloodNet Platform",
                org_type=OrganizationType.PLATFORM,
                contact_email="admin@bloodnet.local",
            )
            platform = repository.verify_organization(platform.id, user.id)
        membership = repository.create_membership(
            user_id=user.id,
            organization_id=platform.id,
            role=fallback_role,
        )
        if not membership:
            membership = repository.get_primary_membership(user.id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No active BloodNet membership")

    organization = repository.get_organization_by_id(membership.organization_id)
    # Repair memberships approved before role-request approval also activated
    # the organization. An active membership with an approval actor is
    # authoritative evidence that a regional administrator approved access.
    # Do not auto-activate any other pending organization state.
    if (
        organization
        and organization.status == "pending"
        and membership.status == MembershipStatus.ACTIVE
        and membership.approved_by
    ):
        organization = repository.verify_organization(
            UUID(organization.id), UUID(membership.approved_by)
        )
    if not organization or organization.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization is not active")

    role = membership.role
    if role in {RoleType.BANK_ADMIN, RoleType.REGIONAL_ADMIN, RoleType.AUDITOR} and user.mfa_enabled and not payload.get("mfa_verified"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="MFA verification required")
    permissions = permissions_for_role(role)
    get_user_location = getattr(repository, "get_user_location", None)
    location = get_user_location(UUID(user.id)) if get_user_location else None

    metadata = getattr(organization, "metadata", None) or {}
    bank_id = resolve_bank_id_from_organization(organization)
    hospital_id = metadata.get("hospital_id") or organization.id if organization.type == "hospital" else None
    region_id = metadata.get("region_id") or metadata.get("region") if organization.type == "regional" else None
    if role == RoleType.REGIONAL_ADMIN and not region_id:
        # Regional administrators may be members of the platform organization.
        # Their approved personal region is the operational scope in that case.
        get_region_preference = getattr(repository, "get_user_region_preference", None)
        region_id = get_region_preference(UUID(user.id)) if get_region_preference else None
    if organization.type == OrganizationType.REGIONAL and not region_id:
        region_id = "Pune"

    return Identity(
        subject_id=user.id,
        email=user.email,
        display_name=user.display_name or user.email,
        role=role,
        organization_id=organization.id,
        permissions=permissions,
        bank_id=bank_id,
        hospital_id=hospital_id,
        region_id=region_id,
        location=location,
    )


def _verify_external_token(token: str) -> dict[str, Any] | None:
    """Verify an Identity Platform/Firebase-compatible ID token."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        audience = get_identity_platform_audience()
        if not audience:
            return None
        return id_token.verify_firebase_token(token, google_requests.Request(), audience=audience)
    except Exception:
        return None


def _verify_gateway_userinfo(request: Request) -> dict[str, Any] | None:
    """Read claims already verified by API Gateway at the protected edge."""
    encoded = request.headers.get("x-apigateway-api-userinfo")
    if not encoded:
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        return payload if isinstance(payload, dict) and payload.get("sub") else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _verify_iap_identity_assertion(iap_assertion: str, request: Request) -> dict[str, Any] | None:
    """Accept a verified IAP identity assertion from the edge and normalize it into a JWT-like payload."""
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        audience = os.getenv("BLOODNET_IAP_AUDIENCE") or get_identity_platform_audience()
        if not audience:
            return None

        payload = id_token.verify_oauth2_token(iap_assertion, google_requests.Request(), audience=audience)
        if not isinstance(payload, dict):
            return None

        email = request.headers.get("x-goog-authenticated-user-email") or payload.get("email")
        if email:
            payload.setdefault("email", email)
        if not payload.get("sub"):
            payload["sub"] = email or payload.get("email") or "iap-user"
        payload.setdefault("mfa_verified", True)
        return payload
    except Exception:
        return None


def require_role(identity: Identity, *roles: str) -> None:
    """Require identity to have one of the specified roles."""
    role_value = identity.role.value if hasattr(identity.role, "value") else str(identity.role)
    if role_value not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This operation requires one of: {', '.join(roles)}"
        )


def require_permission(identity: Identity, *permissions: str) -> None:
    """Require identity to have one of the specified permissions."""
    for perm in permissions:
        if perm in identity.permissions:
            return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"This operation requires one of: {', '.join(permissions)}"
    )


def require_resource_scope(
    identity: Identity,
    resource_id: str | None,
    *,
    scope_type: str | None = None,
    label: str | None = None,
) -> None:
    """Ensure an identity is only acting within its own tenant or resource scope."""
    if resource_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{(label or scope_type or 'Resource').title()} is required for this request",
        )

    current_scope = resource_id
    if scope_type in {None, "organization"}:
        current_scope = identity.organization_id
    elif scope_type == "bank":
        current_scope = identity.bank_id
    elif scope_type == "hospital":
        current_scope = identity.hospital_id
    elif scope_type == "region":
        current_scope = identity.region_id
    elif scope_type == "user":
        current_scope = identity.subject_id
    else:
        current_scope = getattr(identity, f"{scope_type}_id", None)

    if current_scope and str(current_scope) == str(resource_id):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"{(label or scope_type or 'Resource').title()} is outside your resource scope",
    )


def resolve_bank_id_from_organization(organization: Any) -> str | None:
    """Normalize the bank_id claim for a blood-bank organization.

    The inventory and reservation endpoints gate a bank-admin workspace by the
    authenticated bank_id claim. Legacy or partially-onboarded organizations
    sometimes carry no explicit metadata['bank_id']; in that case, preserve
    the organization-backed authorization boundary by deriving the bank scope
    from the organization record instead of dropping it entirely.
    """
    if getattr(organization, "type", None) not in {OrganizationType.BLOOD_BANK, "blood_bank"}:
        return None
    metadata = getattr(organization, "metadata", None) or {}
    if isinstance(metadata, dict):
        bank_id = metadata.get("bank_id")
        if isinstance(bank_id, str) and bank_id.strip():
            return bank_id.strip()
        if isinstance(bank_id, int):
            return str(bank_id)

    # Blood bank organizations are the only type that may carry a bank scope.
    # If the organization record is externalized or missing a metadata field,
    # keep the identity derivation deterministic by reusing the organization id
    # as the resource scope key. This avoids the bank-admin token losing the
    # inventory resource scoping that the BankSurfaceEnhanced endpoints require.
    if getattr(organization, "type", None) in {
        OrganizationType.BLOOD_BANK,
        "blood_bank",
    }:
        return str(getattr(organization, "id", "")) or None
    return None


def validate_service_identity(claims: dict[str, Any]) -> bool:
    """Validate a trusted service-to-service identity claim set."""
    if not isinstance(claims, dict):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service identity is missing required claims")

    service_account = claims.get("sub") or claims.get("azp")
    if isinstance(service_account, str) and service_account.startswith("serviceAccount:"):
        service_account = service_account.split(":", 1)[1]

    trusted_accounts = {
        item.strip()
        for item in (os.getenv("BLOODNET_TRUSTED_SERVICE_ACCOUNTS") or "").split(",")
        if item.strip()
    }
    if not service_account:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service identity is missing a service account subject")
    if not trusted_accounts or service_account not in trusted_accounts:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service identity is not trusted")

    audience = os.getenv("BLOODNET_SERVICE_AUDIENCE") or os.getenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE") or "bloodnet-api"
    aud_claim = claims.get("aud")
    if isinstance(aud_claim, list):
        aud_values = aud_claim
    elif aud_claim is not None:
        aud_values = [aud_claim]
    else:
        aud_values = []
    if aud_values and audience not in aud_values:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service identity audience is not trusted")

    if claims.get("azp") and claims.get("azp") != service_account:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service identity principal mismatch")

    return True


def case_is_in_scope(identity: Identity, case: Case, request_data: dict[str, Any]) -> bool:
    """
    Check if a case is within the identity's access scope.
    
    Implements resource scoping per spec §12:
    - Hospital coordinator: their hospital's cases
    - Bank admin: their bank's inventory matches
    - Regional admin: their region's cases
    - Auditor: all cases
    """
    if identity.role == RoleType.HOSPITAL_COORDINATOR or identity.role == "hospital_coordinator":
        return bool(identity.hospital_id) and request_data.get("hospital_id") == identity.hospital_id
    
    if identity.role == RoleType.BANK_ADMIN or identity.role == "bank_admin":
        if not identity.bank_id:
            return False
        return any(match.bank_id == identity.bank_id for match in case.inventory_matches)
    
    if identity.role == RoleType.REGIONAL_ADMIN or identity.role == "regional_admin":
        requested_region = str(request_data.get("region") or "").strip()
        identity_region = str(identity.region_id or "").strip()
        # A missing request region must never widen a regional administrator's
        # scope. Legacy requests without one remain available to auditors.
        # Compare case-insensitively so metadata normalization and human-facing
        # region labels do not silently widen or narrow the administrator's view.
        return bool(identity_region) and requested_region.casefold() == identity_region.casefold()
    
    # Auditor and platform admin can see everything
    if identity.role in [RoleType.AUDITOR, "auditor"]:
        return True
    
    return False
