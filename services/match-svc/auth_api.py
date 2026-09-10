"""
Authentication and authorization endpoints for BloodNet.

Implements the auth API per spec §1-5:
- POST /api/v1/auth/signup - User registration
- POST /api/v1/auth/verify-email - Email verification
- POST /api/v1/auth/login - User login
- POST /api/v1/auth/password-reset - Password reset request
- POST /api/v1/auth/password-reset-confirm - Password reset completion
- GET /api/v1/auth/me - Current user profile

Spec reference: Auth & Registration Spec §1-5
"""

import os
import hashlib
import time
import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID


def _six_months_after(value: datetime) -> datetime:
    month = value.month - 1 + 6
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _next_eligible_at(last_donation_at: datetime | None) -> datetime | None:
    """Return a concrete next-eligible timestamp for donors who have never donated yet."""
    if last_donation_at is None:
        return datetime.now(timezone.utc)
    return _six_months_after(last_donation_at)

from fastapi import APIRouter, Depends, HTTPException, Request, status
import psycopg
from psycopg.rows import dict_row

from contracts.auth import get_identity, require_permission, require_role, resolve_bank_id_from_organization
from contracts.location import CITY_COORDINATES, enrich_location_metadata, operational_region
from contracts.auth_repository import AuthRepository
from contracts.auth_permissions import permissions_for_role
from contracts.auth_utils import (
    generate_email_verification_token,
    generate_jwt_token,
    generate_password_reset_token,
    hash_password,
    validate_email,
    validate_password,
    verify_email_token_hash,
    verify_password,
    verify_password_reset_token_hash,
    verify_jwt_token,
)
from contracts.email_service import send_email
from contracts.models import (
    AuditEventType,
    Identity,
    InvitationAcceptRequest,
    InvitationCreateRequest,
    InvitationStatus,
    Organization,
    OrganizationCreateRequest,
    OrganizationRegionUpdateRequest,
    OrganizationUpdateRequest,
    OrganizationType,
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenResponse,
    User,
    UserLoginRequest,
    UserProfileResponse,
    UserRegisterRequest,
    UserStatus,
    MembershipStatus,
    RoleType,
    MfaEnrollResponse,
    MfaVerifyRequest,
    DonorProfileRequest,
    DonorProfileResponse,
    DonorDonationCompleteRequest,
    RoleAccessRequest,
    UserLocationRequest,
    UserRegionPreferenceRequest,
)

# Router setup
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
organization_router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])
invitation_router = APIRouter(prefix="/api/v1/invitations", tags=["invitations"])

PRIVILEGED_ROLES = {RoleType.BANK_ADMIN, RoleType.REGIONAL_ADMIN, RoleType.AUDITOR}
REQUESTABLE_ROLES = PRIVILEGED_ROLES | {RoleType.HOSPITAL_COORDINATOR}


def _require_role_approver(identity: Identity) -> None:
    """Only a server-derived regional administrator can approve access."""
    if identity.role != RoleType.REGIONAL_ADMIN:
        raise HTTPException(status_code=403, detail="Role approval requires a regional administrator")

# In-memory rate-limit tracking for the auth boundary. This is intentionally small,
# conservative, and meant to be replaced by a managed edge or Redis rate limiter in production.
_AUTH_RATE_LIMITS: dict[str, list[float]] = {}
_AUTH_RATE_LIMIT_WINDOW_SECONDS = 900
_AUTH_RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("BLOODNET_AUTH_RATE_LIMIT_MAX_ATTEMPTS", "5"))

# Database setup
database_url = os.getenv("BLOODNET_DATABASE_URL")
if not database_url:
    raise RuntimeError("BLOODNET_DATABASE_URL environment variable not set")

auth_repo = AuthRepository(database_url)

def send_email_verification(email: str, token: str, token_hash: str) -> None:
    """Send email verification message."""
    origin = os.getenv("BLOODNET_FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/")
    send_email(recipient=email, subject="Verify your BloodNet email", text=f"Verify your email: {origin}/?mode=verify&token={token}")


def send_password_reset(email: str, token: str, token_hash: str) -> None:
    """Send password reset message."""
    origin = os.getenv("BLOODNET_FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/")
    send_email(recipient=email, subject="Reset your BloodNet password", text=f"Reset your password: {origin}/?mode=reset&token={token}")


def send_invitation(email: str, token: str, organization_name: str) -> None:
    """Send an organization invitation message."""
    origin = os.getenv("BLOODNET_FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/")
    send_email(recipient=email, subject=f"Invitation to join {organization_name}", text=f"Accept your invitation: {origin}/?invitation={token}")


def _normalize_region_for_preference(region_id: str | None) -> str:
    """Normalize an operational city into the stored personal service-region label."""
    raw = " ".join((region_id or "").strip().split())
    if not raw:
        return ""

    raw_slug = raw.lower().replace(" ", "-")
    for city in CITY_COORDINATES:
        if city.casefold().replace(" ", "-") == raw_slug:
            return city

    for city in CITY_COORDINATES:
        if city.casefold() == raw.casefold():
            return city

    return raw


def send_role_request_notice(request_row: dict, user: User, organization: Organization) -> None:
    """Notify the configured operator without making email an authorization boundary."""
    approver = os.getenv("BLOODNET_ROLE_APPROVER_EMAIL")
    if approver:
        send_email(
            recipient=approver,
            subject=f"BloodNet role request: {request_row['requested_role']}",
            text=f"{user.email} requested {request_row['requested_role']} access for {organization.name}. Review it in the BloodNet organization console.",
        )


def _log_login_event(
    request: Request,
    user: User | None,
    event_type: AuditEventType,
    status: str = "success",
) -> None:
    """Persist a login audit event with request metadata."""
    auth_repo.log_audit_event(
        event_type=event_type,
        resource_type="user",
        resource_id=str(user.id) if user else "unknown",
        actor_id=UUID(user.id) if user else None,
        status=status,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


def _rate_limit_key(request: Request, email: str | None = None) -> str:
    """Return a stable identifier for per-IP and per-email throttling."""
    ip_address = request.client.host if request.client else "unknown"
    normalized_email = (email or "").strip().lower()
    return f"{ip_address}:{normalized_email}"


def _check_rate_limit(request: Request, email: str | None = None) -> None:
    """Reject requests that exceed the configured auth rate limit."""
    key = _rate_limit_key(request, email)
    now = time.time()
    attempts = _AUTH_RATE_LIMITS.setdefault(key, [])
    attempts[:] = [ts for ts in attempts if now - ts < _AUTH_RATE_LIMIT_WINDOW_SECONDS]
    if len(attempts) >= _AUTH_RATE_LIMIT_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Try again later.",
        )


def _record_failed_attempt(request: Request, email: str | None = None) -> None:
    """Track a failed auth attempt for the current IP and account."""
    key = _rate_limit_key(request, email)
    now = time.time()
    attempts = _AUTH_RATE_LIMITS.setdefault(key, [])
    attempts[:] = [ts for ts in attempts if now - ts < _AUTH_RATE_LIMIT_WINDOW_SECONDS]
    attempts.append(now)


# ============================================================================
# SIGNUP
# ============================================================================


@router.post("/signup", response_model=dict, status_code=202)
async def signup(req: UserRegisterRequest):
    """
    User self-registration (donor).
    
    Per spec §2:
    - Self-register as DONOR
    - Returns 202 "Check your email"
    - Email verification required before account activation
    
    Request:
    ```json
    {
      "email": "user@example.com",
      "password": "SecurePass123!",
      "password_confirm": "SecurePass123!",
      "display_name": "John Doe",
      "phone": "+91-9999999999"
    }
    ```
    
    Response: 202 Accepted
    ```json
    {
      "status": "email_unverified",
      "message": "Verification email sent. Check your inbox to activate your account."
    }
    ```
    """
    # Validate inputs
    if not validate_email(req.email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    
    is_valid, error_msg = validate_password(req.password, req.password_confirm)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    # Check email not already registered
    existing_user = auth_repo.get_user_by_email(req.email)
    if existing_user:
        # Don't reveal if email exists (security best practice)
        # But for demo, we can be more helpful
        if existing_user.email_verified:
            raise HTTPException(status_code=409, detail="This email is already registered")
        else:
            raise HTTPException(
                status_code=409,
                detail="This email is pending verification. Check your inbox or request a new verification email."
            )
    
    if req.role != RoleType.DONOR:
        raise HTTPException(status_code=400, detail="Elevated roles must be requested after email verification")

    # Hash password
    password_hash = hash_password(req.password)
    
    # Create user (status=EMAIL_UNVERIFIED)
    user = auth_repo.create_user(
        email=req.email,
        display_name=req.display_name,
        phone=req.phone,
        password_hash=password_hash,
    )
    
    secret_token, token_hash, expiry = generate_email_verification_token()
    auth_repo.create_email_verification_token(
        user_id=UUID(user.id),
        email=user.email,
        token_hash=token_hash,
        expires_at=expiry,
    )
    # In local development the adapter logs the link when SMTP is absent.
    send_email_verification(user.email, secret_token, token_hash)
    return {
        "status": "email_unverified",
        "message": "Verification email sent. Check your inbox to activate your account.",
        "email": user.email,
        "expires_in": "24 hours",
    }


@router.post("/role-requests", response_model=dict, status_code=202)
async def request_role_access(
    req: RoleAccessRequest,
    identity: Identity = Depends(get_identity),
):
    """Request an elevated role after email verification; approval grants membership."""
    if req.role not in REQUESTABLE_ROLES:
        raise HTTPException(status_code=400, detail="This role cannot be requested")
    user = auth_repo.get_user_by_id(UUID(identity.subject_id))
    if not user or not user.email_verified:
        raise HTTPException(status_code=403, detail="Verify your email before requesting role access")

    organization = None
    if req.organization_id:
        try:
            organization = auth_repo.get_organization_by_id(UUID(req.organization_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization_id")
        if not organization:
            raise HTTPException(status_code=404, detail="Organization not found")
    else:
        if req.role == RoleType.HOSPITAL_COORDINATOR:
            expected_type = OrganizationType.HOSPITAL
        elif req.role == RoleType.BANK_ADMIN:
            expected_type = OrganizationType.BLOOD_BANK
        else:
            expected_type = OrganizationType.REGIONAL
        if not req.organization_name:
            raise HTTPException(status_code=400, detail="organization_name is required")
        organization = auth_repo.create_organization(
            name=req.organization_name.strip(), org_type=req.organization_type or expected_type,
            contact_email=user.email, address=req.organization_address,
        )

    try:
        membership = auth_repo.get_membership_by_user_org(UUID(user.id), UUID(organization.id))
        if not membership:
            membership = auth_repo.create_membership(
                user_id=UUID(user.id), organization_id=UUID(organization.id), role=req.role,
                status=MembershipStatus.PENDING_APPROVAL, metadata={"request_notes": req.notes} if req.notes else {},
            )
        elif membership.role != req.role or membership.status not in {MembershipStatus.PENDING_APPROVAL, MembershipStatus.REJECTED}:
            raise HTTPException(status_code=409, detail="You already have membership for this organization")
        request_row = auth_repo.create_role_access_request(UUID(user.id), UUID(organization.id), req.role, req.notes)
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="This role request is already pending")

    auth_repo.update_user(UUID(user.id), status=UserStatus.PENDING_APPROVAL)
    send_role_request_notice(request_row, user, organization)
    return {"status": "pending_approval", "request_id": str(request_row["id"]), "organization_id": organization.id, "role": req.role.value}


@router.get("/role-requests", response_model=list[dict])
async def list_role_requests(identity: Identity = Depends(get_identity)):
    """Return the approval queue to regional administrators."""
    _require_role_approver(identity)

    def _request_uuid(value: UUID | str) -> UUID:
        return value if isinstance(value, UUID) else UUID(str(value))

    requests = auth_repo.list_role_access_requests()
    for item in requests:
        user = auth_repo.get_user_by_id(_request_uuid(item["user_id"]))
        organization = auth_repo.get_organization_by_id(_request_uuid(item["organization_id"]))
        item["user"] = {"email": user.email, "display_name": user.display_name} if user else None
        item["organization"] = {
            "id": organization.id,
            "name": organization.name,
            "type": organization.type.value,
            "region": (organization.metadata or {}).get("region") or (organization.metadata or {}).get("region_id") or organization.address or "Unspecified",
        } if organization else None
    return requests


@router.post("/role-requests/{request_id}/{decision}", response_model=dict)
async def decide_role_request(request_id: UUID, decision: str, identity: Identity = Depends(get_identity)):
    """Approve or reject one pending role request."""
    _require_role_approver(identity)
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Decision must be approve or reject")
    request_row = auth_repo.get_role_access_request(request_id)
    if not request_row or request_row["status"] != "pending":
        raise HTTPException(status_code=404, detail="Pending role request not found")
    user_id = request_row["user_id"] if isinstance(request_row["user_id"], UUID) else UUID(str(request_row["user_id"]))
    organization_id = request_row["organization_id"] if isinstance(request_row["organization_id"], UUID) else UUID(str(request_row["organization_id"]))
    actor_id = identity.subject_id if isinstance(identity.subject_id, UUID) else UUID(str(identity.subject_id))
    membership = auth_repo.get_membership_by_user_org(user_id, organization_id)
    if not membership:
        raise HTTPException(status_code=409, detail="Requested membership no longer exists")
    if decision == "approve":
        membership_id = membership.id if isinstance(membership.id, UUID) else UUID(str(membership.id))
        auth_repo.update_membership_status(membership_id, MembershipStatus.ACTIVE, actor_id)
        # Role requests can create a new organization, which starts in the
        # pending state.  A membership cannot be used until its organization
        # is active, so the same administrator approval must activate it.
        organization = auth_repo.get_organization_by_id(organization_id)
        if organization and organization.status != "active":
            auth_repo.verify_organization(organization_id, actor_id)
        auth_repo.update_user(user_id, status=UserStatus.ACTIVE, updated_by=actor_id)
    else:
        membership_id = membership.id if isinstance(membership.id, UUID) else UUID(str(membership.id))
        auth_repo.update_membership_status(membership_id, MembershipStatus.REJECTED, actor_id)
    auth_repo.update_role_access_request(request_id, "approved" if decision == "approve" else "rejected", actor_id)
    return {"status": "approved" if decision == "approve" else "rejected", "request_id": str(request_id)}


# ============================================================================
# EMAIL VERIFICATION
# ============================================================================


@router.post("/verify-email", response_model=None)
async def verify_email(token: str):
    """
    Verify email with a verification token.
    
    Per spec §7:
    - Verify token is valid and not expired
    - Activate account (status=ACTIVE)
    - Return JWT access token
    
    Query params:
    - token: Email verification token (sent via email link)
    
    Response: 200 OK
    ```json
    {
      "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
      "token_type": "bearer",
      "expires_in": 86400,
      "user": {
        "id": "user-uuid",
        "email": "user@example.com",
        "display_name": "John Doe",
        "role": "donor",
        "status": "active",
        "created_at": "2026-08-27T12:00:00Z"
      }
    }
    ```
    """
    # Verify token is valid and not expired
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    token_record = auth_repo.get_email_verification_token_by_hash(token_hash)
    if not token_record:
        raise HTTPException(status_code=400, detail="Invalid verification token")
    
    if token_record["status"] != "pending":
        raise HTTPException(status_code=400, detail="Verification token already used or expired")
    
    if token_record["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification token has expired")
    
    # Use the token (marks as verified and updates user)
    success = auth_repo.verify_email_token(token_hash)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to verify email. Try again or request a new token.")
    
    # Get the user
    user_id = token_record["user_id"] if isinstance(token_record["user_id"], UUID) else UUID(token_record["user_id"])
    user = auth_repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=500, detail="User not found")
    
    existing_membership = auth_repo.get_primary_membership(user_id)
    if existing_membership and existing_membership.status == MembershipStatus.PENDING_APPROVAL:
        auth_repo.update_user(user_id, status=UserStatus.PENDING_APPROVAL)
        return {"status": "pending_approval", "message": "Email verified. Your organization must approve your account."}

    # Self-registered users receive the canonical donor membership.
    
    # Get or create platform organization
    # (In production, seed this in migrations)
    platform_org_id = None
    
    try:
        # Try to find existing platform org
        with psycopg.connect(database_url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM organizations WHERE type = %s LIMIT 1", ("platform",))
                org_row = cur.fetchone()
                if org_row:
                    platform_org_id = org_row["id"]
    except Exception:
        pass
    
    # If no platform org, create one
    if not platform_org_id:
        platform_org = auth_repo.create_organization(
            name="BloodNet Platform",
            org_type=OrganizationType.PLATFORM,
            contact_email="admin@bloodnet.local",
        )
        auth_repo.verify_organization(UUID(platform_org.id), user_id)
        platform_org_id = platform_org.id
    else:
        platform_org_id = str(platform_org_id)
    
    # Create membership as DONOR
    from contracts.models import RoleType, MembershipStatus
    membership = auth_repo.create_membership(
        user_id=user_id,
        organization_id=UUID(platform_org_id),
        role=RoleType.DONOR,
        status=MembershipStatus.ACTIVE,
    )
    
    # Get organization for response
    org = auth_repo.get_organization_by_id(UUID(platform_org_id))
    
    # Generate JWT token
    access_token, expires_at_timestamp = generate_jwt_token(
        subject_id=user.id,
        email=user.email,
        role=RoleType.DONOR.value,
        organization_id=platform_org_id,
        permissions=permissions_for_role(RoleType.DONOR),
        bank_id=None,
        hospital_id=None,
        region_id=None,
    )
    
    # Build response
    user_profile = UserProfileResponse(
        id=user.id,
        subject_id=user.id,
        email=user.email,
        display_name=user.display_name,
        phone=user.phone,
        status=user.status,
        role=RoleType.DONOR,
        organization={
            "id": org.id,
            "name": org.name,
            "type": org.type.value,
        } if org else None,
        permissions=permissions_for_role(RoleType.DONOR),
        mfa_enabled=user.mfa_enabled,
        created_at=user.created_at,
    )
    
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=24 * 3600,  # 24 hours
        user=user_profile,
    )


# ============================================================================
# LOGIN
# ============================================================================


@router.post("/login", response_model=TokenResponse)
async def login(req: UserLoginRequest, request: Request):
    """
    User login with email and password.

    Per spec §4:
    - Verify email/password
    - Check email verified
    - Load user's primary org membership
    - Generate JWT token
    - Return token + user profile

    Request:
    ```json
    {
      "email": "user@example.com",
      "password": "SecurePass123!"
    }
    ```

    Response: 200 OK (with JWT token)
    ```json
    {
      "access_token": "eyJ0eXAiOiJKV1QiLCJhbGc...",
      "token_type": "bearer",
      "expires_in": 86400,
      "user": {
        "id": "user-uuid",
        "email": "user@example.com",
        "display_name": "John Doe",
        "role": "bank_admin",
        "organization": {
          "id": "org-uuid",
          "name": "Pune Blood Bank",
          "type": "blood_bank"
        },
        "permissions": ["inventory.read", "inventory.reserve", "cases.approve"],
        "status": "active",
        "created_at": "2026-08-27T12:00:00Z"
      }
    }
    ```
    """
    if os.getenv("BLOODNET_AUTH_MODE") in {"production", "identity-platform"}:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Local password login is disabled in production. Use Identity Platform or an authenticated gateway flow.",
        )

    _check_rate_limit(request, req.email)

    # Find user by email
    user = auth_repo.get_user_by_email(req.email)
    if not user or not user.password_hash:
        # Don't reveal if email exists (prevent enumeration)
        _record_failed_attempt(request, req.email)
        _log_login_event(request, user, AuditEventType.LOGIN_FAILURE, status="failure")
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Verify password
    if not verify_password(req.password, user.password_hash):
        _record_failed_attempt(request, user.email)
        _log_login_event(request, user, AuditEventType.LOGIN_FAILURE, status="failure")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    # Check email verified (unless email is not configured)
    smtp_configured = all((
        os.getenv("BLOODNET_SMTP_HOST"),
        os.getenv("BLOODNET_SMTP_USERNAME"),
        os.getenv("BLOODNET_SMTP_PASSWORD"),
        os.getenv("BLOODNET_EMAIL_FROM"),
    ))
    
    if smtp_configured and not user.email_verified:
        _log_login_event(request, user, AuditEventType.LOGIN_FAILURE, status="failure")
        raise HTTPException(
            status_code=403,
            detail="Email not verified. Check your inbox for verification link."
        )
    
    # Check account active
    if user.status != UserStatus.ACTIVE:
        _log_login_event(request, user, AuditEventType.LOGIN_FAILURE, status="failure")
        raise HTTPException(
            status_code=403,
            detail=f"Account is {user.status.value}. Please contact support."
        )
    
    # Self-registered users receive a donor membership, without bank scope.
    membership = auth_repo.get_primary_membership(user.id)
    if not membership:
        org_name = user.display_name or user.email.split("@", 1)[0]
        default_org = auth_repo.create_organization(
            name=f"{org_name}'s Organization",
            org_type=OrganizationType.PLATFORM,
            contact_email=user.email,
            contact_phone=user.phone,
        )
        auth_repo.update_organization(UUID(default_org.id), status="active")
        membership = auth_repo.create_membership(
            user_id=user.id,
            organization_id=UUID(default_org.id),
            role=RoleType.DONOR,
            status=MembershipStatus.ACTIVE,
        )
        if not membership:
            membership = auth_repo.get_primary_membership(user.id)

    if not membership:
        _log_login_event(request, user, AuditEventType.LOGIN_FAILURE, status="failure")
        raise HTTPException(
            status_code=403,
            detail="User has no active organization membership"
        )
    
    # Get organization
    org = auth_repo.get_organization_by_id(UUID(membership.organization_id))
    if not org:
        _log_login_event(request, user, AuditEventType.LOGIN_FAILURE, status="failure")
        raise HTTPException(status_code=500, detail="Organization not found")
    
    # Organization scope should not disappear when persisted organization
    # metadata is missing a bank_id field. Normalize the bank resource scope
    # from the organization record so the issued JWT stays consistent with the
    # inventory endpoints and bank-admin resource checks.
    bank_id = resolve_bank_id_from_organization(org)
    hospital_id = (org.metadata or {}).get("hospital_id") or org.id if org.type.value == "hospital" else None
    region_id = operational_region(org.metadata) if org.type.value == "regional" else None

    permissions = permissions_for_role(membership.role)
    
    # Generate JWT token
    access_token, expires_at_timestamp = generate_jwt_token(
        subject_id=user.id,
        email=user.email,
        role=membership.role.value,
        organization_id=membership.organization_id,
        permissions=permissions,
        bank_id=bank_id,
        hospital_id=hospital_id,
        region_id=region_id,
    )
    
    # Build response
    user_profile = UserProfileResponse(
        id=user.id,
        subject_id=user.id,
        email=user.email,
        display_name=user.display_name,
        phone=user.phone,
        status=user.status,
        role=membership.role,
        organization={
            "id": org.id,
            "name": org.name,
            "type": org.type.value,
        },
        bank_id=bank_id,
        hospital_id=hospital_id,
        region_id=region_id,
        permissions=permissions,
        mfa_enabled=user.mfa_enabled,
        created_at=user.created_at,
    )

    _log_login_event(request, user, AuditEventType.LOGIN_SUCCESS, status="success")

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=24 * 3600,  # 24 hours
        user=user_profile,
    )


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
async def enroll_mfa(identity: Identity = Depends(get_identity)):
    """Create a TOTP secret for the current privileged account."""
    if identity.role not in PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="MFA enrollment is only required for privileged roles")
    import pyotp
    secret = pyotp.random_base32()
    uri = pyotp.TOTP(secret).provisioning_uri(name=identity.email, issuer_name="BloodNet")
    auth_repo.update_user(UUID(identity.subject_id), updated_by=UUID(identity.subject_id), mfa_enabled=True, mfa_secret=secret)
    return MfaEnrollResponse(secret=secret, provisioning_uri=uri)


@router.post("/mfa/verify", response_model=dict)
async def verify_mfa(req: MfaVerifyRequest, request: Request):
    """Verify a TOTP code for an enrolled account."""
    import pyotp
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization[7:]
    payload = verify_jwt_token(token) or _verify_external_token_for_mfa(token)
    subject_id = payload.get("sub") if payload else None
    user = auth_repo.get_user_by_identity_subject(subject_id) if subject_id else None
    if not user and subject_id:
        user = auth_repo.get_user_by_id(subject_id)
    if not user or not user.mfa_secret or not pyotp.TOTP(user.mfa_secret).verify(req.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    membership = auth_repo.get_primary_membership(user.id)
    if not membership:
        raise HTTPException(status_code=403, detail="User has no active organization membership")
    organization = auth_repo.get_organization_by_id(UUID(membership.organization_id))
    permissions = permissions_for_role(membership.role)
    bank_id = resolve_bank_id_from_organization(organization)
    region_id = operational_region(organization.metadata) if organization and organization.type.value == "regional" else None
    access_token, expires_at = generate_jwt_token(
        subject_id=user.id, email=user.email, role=membership.role.value,
        organization_id=membership.organization_id, permissions=permissions,
        bank_id=bank_id,
        hospital_id=organization.id if organization and organization.type.value == "hospital" else None,
        region_id=region_id,
        mfa_verified=True,
    )
    return {"status": "verified", "access_token": access_token, "expires_in": expires_at - int(datetime.now(timezone.utc).timestamp())}


def _verify_external_token_for_mfa(token: str) -> dict | None:
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
        audience = os.getenv("BLOODNET_IDENTITY_PLATFORM_AUDIENCE") or os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
        return id_token.verify_firebase_token(token, google_requests.Request(), audience=audience) if audience else None
    except Exception:
        return None


@organization_router.post("", response_model=Organization, status_code=201)
async def create_organization(
    req: OrganizationCreateRequest,
    identity: Identity = Depends(get_identity),
):
    """Create an organization pending platform verification."""
    require_role(identity, "regional_admin")
    require_permission(identity, "organizations.create")

    if not req.name.strip() or not validate_email(req.contact_email):
        raise HTTPException(status_code=400, detail="Valid name and contact email are required")

    return auth_repo.create_organization(
        name=req.name.strip(),
        org_type=req.type,
        contact_email=req.contact_email,
        contact_phone=req.contact_phone,
        address=req.address,
        metadata=enrich_location_metadata(req.metadata),
    )


@organization_router.get("/me", response_model=Organization)
async def get_current_organization(identity: Identity = Depends(get_identity)):
    """Return the authenticated user's current organization profile."""
    if not identity.organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    organization = auth_repo.get_organization_by_id(UUID(identity.organization_id))
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


@organization_router.patch("/me", response_model=Organization)
async def update_current_organization(
    req: OrganizationUpdateRequest,
    identity: Identity = Depends(get_identity),
):
    """Update the authenticated user's organization profile."""
    if identity.role not in {RoleType.HOSPITAL_COORDINATOR, RoleType.BANK_ADMIN}:
        raise HTTPException(status_code=403, detail="Only hospital and bank administrators can update organization details")
    if not identity.organization_id:
        raise HTTPException(status_code=404, detail="Organization not found")
    organization = auth_repo.get_organization_by_id(UUID(identity.organization_id))
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if req.name is not None and not req.name.strip():
        raise HTTPException(status_code=422, detail="Organization name is required")
    if req.contact_email is not None and not validate_email(req.contact_email):
        raise HTTPException(status_code=400, detail="Valid contact email is required")

    metadata = dict(organization.metadata or {})
    if req.metadata:
        for key in ("bank_id", "hospital_id", "region_id", "region", "city"):
            if key in req.metadata and req.metadata[key] != metadata.get(key):
                raise HTTPException(status_code=403, detail="Resource scope changes require regional administration")
        metadata.update(req.metadata)
    if req.region_id is not None:
        if req.region_id.strip() != str(metadata.get("region_id") or ""):
            raise HTTPException(status_code=403, detail="Resource scope changes require regional administration")
        metadata["region_id"] = req.region_id.strip()
    if req.registration_id is not None:
        metadata["registration_id"] = req.registration_id.strip()
    if req.facility_location is not None:
        metadata["geo"] = {"lat": req.facility_location.lat, "lng": req.facility_location.lng}
        metadata["location_source"] = "user_confirmed_facility"
    if "geo" in metadata:
        from contracts.models import UserLocation
        from pydantic import ValidationError
        try:
            validated_geo = UserLocation.model_validate(metadata["geo"])
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="Facility latitude or longitude is invalid") from exc
        metadata["geo"] = {"lat": validated_geo.lat, "lng": validated_geo.lng}

    normalized_metadata = metadata or None
    return auth_repo.update_organization(
        UUID(organization.id),
        name=req.name.strip() if req.name is not None else None,
        address=req.address.strip() if req.address is not None else None,
        contact_email=req.contact_email.strip() if req.contact_email is not None else None,
        contact_phone=req.contact_phone.strip() if req.contact_phone is not None else None,
        metadata=normalized_metadata,
    )


@organization_router.get("", response_model=list[Organization])
async def list_organizations(
    status_filter: str | None = None,
    identity: Identity = Depends(get_identity),
):
    require_role(identity, "regional_admin")
    return auth_repo.list_organizations(status_filter)


@organization_router.patch("/{organization_id}/region", response_model=Organization)
async def update_organization_region(
    organization_id: UUID,
    req: OrganizationRegionUpdateRequest,
    identity: Identity = Depends(get_identity),
):
    """Set the explicit operational region used by proximity and case scope."""
    require_role(identity, "regional_admin")
    organization = auth_repo.get_organization_by_id(organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    region_id = "-".join(req.region_id.strip().lower().split())
    if not region_id:
        raise HTTPException(status_code=422, detail="A region is required")
    metadata = enrich_location_metadata({**(organization.metadata or {}), "city": region_id, "region_id": region_id})
    return auth_repo.update_organization(organization_id, metadata=metadata)


@organization_router.get("/{organization_id}/members", response_model=list[dict])
async def list_organization_members(
    organization_id: UUID,
    status_filter: str | None = None,
    identity: Identity = Depends(get_identity),
):
    require_role(identity, "regional_admin")
    organization = auth_repo.get_organization_by_id(organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    memberships = auth_repo.list_memberships(organization_id, MembershipStatus(status_filter) if status_filter else None)
    return [
        {"membership": membership.model_dump(mode="json"), "user": (auth_repo.get_user_by_id(UUID(membership.user_id))).model_dump(mode="json") if auth_repo.get_user_by_id(UUID(membership.user_id)) else None}
        for membership in memberships
    ]


@organization_router.post("/{organization_id}/members/{membership_id}/role", response_model=dict)
async def assign_membership_role(
    organization_id: UUID,
    membership_id: UUID,
    role: RoleType,
    identity: Identity = Depends(get_identity),
):
    require_role(identity, "regional_admin")
    organization = auth_repo.get_organization_by_id(organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    memberships = auth_repo.list_memberships(organization_id)
    membership = next((item for item in memberships if item.id == str(membership_id)), None)
    if not membership:
        raise HTTPException(status_code=404, detail="Membership not found")
    auth_repo.update_membership_role(membership_id, role, UUID(identity.subject_id))
    if organization.status != "active":
        auth_repo.verify_organization(organization_id, UUID(identity.subject_id))
    return {"status": "updated", "membership_id": str(membership_id), "role": role.value}


@organization_router.post("/onboard", response_model=dict, status_code=202)
async def onboard_organization(req: OrganizationCreateRequest):
    """Register an organization and its first coordinator/admin for approval."""
    if req.type not in {OrganizationType.HOSPITAL, OrganizationType.BLOOD_BANK}:
        raise HTTPException(status_code=400, detail="Only hospitals and blood banks can self-onboard")
    owner_email = req.owner_email
    owner_name = req.owner_name
    owner_password = req.owner_password
    owner_password_confirm = req.owner_password_confirm or owner_password
    if not owner_email or not owner_name or not owner_password:
        raise HTTPException(status_code=400, detail="owner_email, owner_name, and owner_password are required")
    if not validate_email(owner_email):
        raise HTTPException(status_code=400, detail="Invalid owner email address")
    valid, error = validate_password(owner_password, owner_password_confirm)
    if not valid:
        raise HTTPException(status_code=400, detail=error)
    if auth_repo.get_user_by_email(owner_email):
        raise HTTPException(status_code=409, detail="Owner email is already registered")

    if req.metadata and any(key in req.metadata for key in ("bank_id", "hospital_id")):
        raise HTTPException(status_code=400, detail="Facility identifiers are assigned by the server")

    organization = auth_repo.create_organization(
        name=req.name.strip(), org_type=req.type, contact_email=req.contact_email,
        contact_phone=req.contact_phone, address=req.address,
        metadata=req.metadata,
    )
    owner = auth_repo.create_user(
        email=owner_email, display_name=owner_name, password_hash=hash_password(owner_password),
    )
    role = RoleType.HOSPITAL_COORDINATOR if req.type == OrganizationType.HOSPITAL else RoleType.BANK_ADMIN
    auth_repo.create_membership(
        user_id=UUID(owner.id), organization_id=UUID(organization.id), role=role,
        status=MembershipStatus.PENDING_APPROVAL,
    )

    # Persist the organization’s selected operational region as the owner’s
    # personal service-region preference instantly during onboarding. This
    # makes the region UI start as saved and still editable from the account panel.
    region_hint = None
    if req.metadata:
        region_hint = req.metadata.get("region_id") or req.metadata.get("region") or req.metadata.get("city")
    if region_hint:
        auth_repo.upsert_user_region_preference(UUID(owner.id), _normalize_region_for_preference(region_hint))

    secret_token, token_hash, expiry = generate_email_verification_token()
    auth_repo.create_email_verification_token(UUID(owner.id), owner.email, token_hash, expiry)
    send_email_verification(owner.email, secret_token, token_hash)
    return {"status": "pending_approval", "organization_id": organization.id, "email": owner.email}


@organization_router.post("/{organization_id}/members/{membership_id}/approve", response_model=dict)
async def approve_membership(
    organization_id: UUID,
    membership_id: UUID,
    identity: Identity = Depends(get_identity),
):
    """Approve a pending coordinator or bank-admin membership."""
    require_role(identity, "regional_admin")
    organization = auth_repo.get_organization_by_id(organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    membership = auth_repo.update_membership_status(
        membership_id, MembershipStatus.ACTIVE, UUID(identity.subject_id)
    )
    # Keep the organization and membership approval paths consistent: an
    # active membership must never point at a pending organization.
    if organization.status != "active":
        auth_repo.verify_organization(organization_id, UUID(identity.subject_id))
    auth_repo.update_user(UUID(membership.user_id), status=UserStatus.ACTIVE, updated_by=UUID(identity.subject_id))
    return {"status": "active", "membership_id": membership.id, "user_id": membership.user_id}


@organization_router.post("/{organization_id}/verify", response_model=Organization)
async def verify_organization(
    organization_id: UUID,
    identity: Identity = Depends(get_identity),
):
    """Verify a pending organization as a platform administrator."""
    require_role(identity, "regional_admin")
    organization = auth_repo.get_organization_by_id(organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")
    if organization.verified:
        return organization

    return auth_repo.verify_organization(
        organization_id,
        UUID(identity.subject_id),
    )


@invitation_router.post("", response_model=dict, status_code=201)
async def create_invitation(
    req: InvitationCreateRequest,
    identity: Identity = Depends(get_identity),
):
    """Invite a user to join an organization."""
    require_role(identity, "hospital_coordinator", "bank_admin", "regional_admin")
    organization_id = UUID(req.organization_id)
    if identity.role != RoleType.REGIONAL_ADMIN and identity.organization_id != str(organization_id):
        raise HTTPException(status_code=403, detail="You can only invite users to your organization")
    if not validate_email(req.email):
        raise HTTPException(status_code=400, detail="Invalid email address")
    organization = auth_repo.get_organization_by_id(organization_id)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    existing_user = auth_repo.get_user_by_email(req.email)
    if existing_user and auth_repo.get_membership_by_user_org(UUID(existing_user.id), organization_id):
        raise HTTPException(status_code=409, detail="User is already a member of this organization")

    invitation, secret_token = auth_repo.create_invitation(
        organization_id=organization_id,
        email=req.email,
        role=req.role,
        invited_by=UUID(identity.subject_id),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    send_invitation(req.email, secret_token, organization.name)
    return {
        "id": invitation.id,
        "status": invitation.status.value,
        "email": invitation.email,
        "role": invitation.role.value,
        "token": secret_token,
        "expires_at": invitation.expires_at,
    }


@invitation_router.post("/{token}/accept", response_model=TokenResponse)
async def accept_invitation(token: str, req: InvitationAcceptRequest):
    """Accept an invitation and activate the organization membership."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    invitation = auth_repo.get_invitation_by_token_hash(token_hash)
    if not invitation:
        raise HTTPException(status_code=400, detail="Invalid invitation token")
    if invitation.status != InvitationStatus.PENDING:
        raise HTTPException(status_code=400, detail="Invitation already used or invalid")
    if invitation.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Invitation has expired")

    is_valid, error_msg = validate_password(req.password, req.password_confirm)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    organization = auth_repo.get_organization_by_id(UUID(invitation.organization_id))
    if not organization:
        raise HTTPException(status_code=500, detail="Organization not found")

    user = auth_repo.get_user_by_email(invitation.email)
    password_hash = hash_password(req.password)
    if not user:
        user = auth_repo.create_user(
            email=invitation.email,
            display_name=req.display_name,
            password_hash=password_hash,
        )
    else:
        if auth_repo.get_membership_by_user_org(UUID(user.id), UUID(invitation.organization_id)):
            raise HTTPException(status_code=409, detail="User is already a member of this organization")
    auth_repo.update_user(
        UUID(user.id),
        display_name=req.display_name if req.display_name else None,
        password_hash=password_hash if not user.password_hash else None,
        status=UserStatus.ACTIVE,
        email_verified=True,
    )
    user = auth_repo.get_user_by_id(UUID(user.id))
    membership = auth_repo.create_membership(
        user_id=UUID(user.id),
        organization_id=UUID(invitation.organization_id),
        role=invitation.role,
        invited_by=UUID(invitation.invited_by),
        status=MembershipStatus.ACTIVE,
    )
    auth_repo.accept_invitation(UUID(invitation.id), UUID(user.id))
    permissions = permissions_for_role(membership.role)
    bank_id = resolve_bank_id_from_organization(organization)
    region_id = ((organization.metadata or {}).get("region_id") or (organization.metadata or {}).get("region")) if organization.type == OrganizationType.REGIONAL else None
    access_token, expires_at_timestamp = generate_jwt_token(
        subject_id=user.id,
        email=user.email,
        role=membership.role.value,
        organization_id=organization.id,
        permissions=permissions,
        bank_id=bank_id,
        hospital_id=organization.id if organization.type == OrganizationType.HOSPITAL else None,
        region_id=region_id,
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=expires_at_timestamp - int(datetime.now(timezone.utc).timestamp()),
        user=UserProfileResponse(
            id=user.id,
            subject_id=user.id,
            email=user.email,
            display_name=user.display_name,
            phone=user.phone,
            status=user.status,
            role=membership.role,
            organization={"id": organization.id, "name": organization.name, "type": organization.type.value},
            bank_id=bank_id,
            hospital_id=organization.id if organization.type == OrganizationType.HOSPITAL else None,
            region_id=region_id,
            permissions=permissions,
            mfa_enabled=user.mfa_enabled,
            created_at=user.created_at,
        ),
    )


# ============================================================================
# PASSWORD RESET
# ============================================================================


@router.post("/password-reset", response_model=dict, status_code=202)
async def password_reset(req: PasswordResetRequest):
    """
    Initiate password reset.
    
    Per spec §8:
    - Find user by email (don't reveal if exists)
    - Generate reset token (15 min expiry)
    - Send reset email
    - Return 200 (always, prevent enumeration)
    
    Request:
    ```json
    {
      "email": "user@example.com"
    }
    ```
    
    Response: 202 Accepted
    ```json
    {
      "status": "ok",
      "message": "If the email exists, a password reset link has been sent."
    }
    ```
    """
    # Find user (don't reveal if exists)
    user = auth_repo.get_user_by_email(req.email)
    
    if user and user.password_hash:
        # Generate reset token
        secret_token, token_hash, expiry = generate_password_reset_token()
        
        # Store token in database
        auth_repo.create_password_reset_token(
            user_id=UUID(user.id),
            token_hash=token_hash,
            expires_at=expiry,
        )
        
        # Send email
        send_password_reset(user.email, secret_token, token_hash)
    
    # Always return success (prevent email enumeration)
    return {
        "status": "ok",
        "message": "If the email exists, a password reset link has been sent. Check your inbox.",
        "expires_in": "15 minutes",
    }


@router.post("/password-reset-confirm", response_model=dict, status_code=200)
async def password_reset_confirm(req: PasswordResetConfirm):
    """
    Complete password reset with token.
    
    Per spec §8:
    - Verify token + expiry
    - Validate new password
    - Hash + update DB
    - Invalidate all reset tokens
    - Return 200
    
    Request:
    ```json
    {
      "token": "reset-token-from-email",
      "password": "NewSecurePass123!",
      "password_confirm": "NewSecurePass123!"
    }
    ```
    
    Response: 200 OK
    ```json
    {
      "status": "ok",
      "message": "Password has been reset. You can now log in with your new password."
    }
    ```
    """
    # Validate new password
    is_valid, error_msg = validate_password(req.password, req.password_confirm)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    
    # Get the token record
    token_hash = hashlib.sha256(req.token.encode()).hexdigest()
    token_record = auth_repo.get_password_reset_token_by_hash(token_hash)
    if not token_record:
        raise HTTPException(
            status_code=400,
            detail="Invalid password reset token. Request a new one."
        )
    
    # Check token status and expiry
    if token_record["status"] != "pending":
        raise HTTPException(
            status_code=400,
            detail="Password reset token has already been used or is invalid."
        )
    
    if token_record["expires_at"] < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="Password reset token has expired. Request a new one."
        )
    
    # Hash the new password
    password_hash = hash_password(req.password)
    
    # Use the token and update password
    success = auth_repo.use_password_reset_token(
        token_hash=token_hash,
        new_password_hash=password_hash,
    )
    
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Failed to reset password. Please try again."
        )
    
    return {
        "status": "ok",
        "message": "Password has been reset successfully. You can now log in with your new password.",
    }


# ============================================================================
# CURRENT USER PROFILE
# ============================================================================


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user(identity: Identity = Depends(get_identity)):
    """
    Get current authenticated user's profile.
    
    Per spec §5:
    - Load user from database
    - Include role, organization, permissions
    - Return comprehensive user profile
    
    Returns:
    ```json
    {
      "id": "user-uuid",
      "email": "user@example.com",
      "display_name": "John Doe",
      "role": "bank_admin",
      "status": "active",
      "organization": {
        "id": "org-uuid",
        "name": "Pune Blood Bank",
        "type": "blood_bank"
      },
      "permissions": ["inventory.read", "cases.approve"],
      "mfa_enabled": false,
      "created_at": "2026-08-27T12:00:00Z"
    }
    ```
    """
    def _lookup_user(user_id: str):
        try:
            return auth_repo.get_user_by_id(UUID(user_id))
        except (TypeError, ValueError):
            return auth_repo.get_user_by_id(user_id)

    def _lookup_org(org_id: str | None):
        if not org_id:
            return None
        try:
            return auth_repo.get_organization_by_id(UUID(org_id))
        except (TypeError, ValueError):
            return auth_repo.get_organization_by_id(org_id)

    user = _lookup_user(identity.subject_id)
    org = _lookup_org(identity.organization_id)

    profile = UserProfileResponse(
        id=user.id if user else identity.subject_id,
        subject_id=user.id if user else identity.subject_id,
        email=user.email if user else identity.email,
        display_name=user.display_name if user else identity.display_name,
        phone=user.phone if user else None,
        status=user.status if user else UserStatus.ACTIVE,
        role=identity.role,
        organization={
            "id": org.id,
            "name": org.name,
            "type": org.type.value,
        } if org else None,
        bank_id=identity.bank_id,
        hospital_id=identity.hospital_id,
        region_id=identity.region_id,
        location=identity.location,
        permissions=identity.permissions,
        mfa_enabled=user.mfa_enabled if user else False,
        created_at=user.created_at if user else datetime.now(timezone.utc),
    )

    return profile


@router.put("/location", response_model=UserLocationRequest)
async def save_user_location(payload: UserLocationRequest, identity: Identity = Depends(get_identity)):
    """Store the latest consented device location for proximity workflows."""
    return auth_repo.upsert_user_location(UUID(identity.subject_id), payload)


@router.get("/region-preference")
async def get_user_region_preference(identity: Identity = Depends(get_identity)):
    """Get the user's own service-region preference, separate from access scope."""
    return {"region_id": auth_repo.get_user_region_preference(UUID(identity.subject_id))}


@router.put("/region-preference")
async def save_user_region_preference(
    payload: UserRegionPreferenceRequest,
    identity: Identity = Depends(get_identity),
):
    """Save a personal service-region preference without changing permissions."""
    region_id = _normalize_region_for_preference(payload.region_id)
    if not region_id:
        raise HTTPException(status_code=422, detail="A region is required")
    if identity.role == RoleType.REGIONAL_ADMIN and (
        not identity.region_id or region_id.casefold() != identity.region_id.casefold()
    ):
        raise HTTPException(status_code=403, detail="Your assigned administrative region cannot be changed through preferences")
    return {"region_id": auth_repo.upsert_user_region_preference(UUID(identity.subject_id), region_id)}


@router.get("/donor-profile", response_model=DonorProfileResponse)
async def get_donor_profile(identity: Identity = Depends(get_identity)):
    require_role(identity, RoleType.DONOR)
    user_id = UUID(identity.subject_id)
    profile = auth_repo.get_donor_profile(user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Donor onboarding is incomplete")
    user = auth_repo.get_user_by_id(user_id)
    region_id = auth_repo.get_user_region_preference(user_id) or profile.get("city") or ""
    profile_payload = {**profile, "region_id": region_id}
    return DonorProfileResponse(**profile_payload, display_name=user.display_name if user else identity.display_name, phone=user.phone if user else None)


@router.put("/donor-profile", response_model=DonorProfileResponse)
async def save_donor_profile(payload: DonorProfileRequest, identity: Identity = Depends(get_identity)):
    require_role(identity, RoleType.DONOR)
    if payload.date_of_birth is None:
        raise HTTPException(status_code=400, detail="Date of birth is required to verify that the donor is at least 18")
    if payload.date_of_birth is not None:
        today = datetime.now(timezone.utc).date()
        age = today.year - payload.date_of_birth.year - (
            (today.month, today.day) < (payload.date_of_birth.month, payload.date_of_birth.day)
        )
        if age < 18:
            raise HTTPException(status_code=400, detail="Donors must be at least 18 years old")
    if payload.availability not in {"available", "paused"}:
        raise HTTPException(status_code=400, detail="Availability must be available or paused")
    if not set(payload.notification_channels).issubset({"email"}):
        raise HTTPException(status_code=400, detail="Email is the only supported MVP notification channel")

    city = " ".join(payload.city.strip().split())
    region_id = " ".join(payload.region_id.strip().split())
    if not city:
        raise HTTPException(status_code=422, detail="A donor location is required")
    if not region_id:
        raise HTTPException(status_code=422, detail="A donor service region is required")

    user_id = UUID(identity.subject_id)
    existing = auth_repo.get_donor_profile(user_id) or {}
    last_donation = payload.last_donation_at
    if last_donation is not None:
        last_donation = last_donation.replace(tzinfo=timezone.utc) if last_donation.tzinfo is None else last_donation
        if last_donation > datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Last donation cannot be in the future")
    recorded = existing.get("last_donation_at")
    if isinstance(recorded, str):
        recorded = datetime.fromisoformat(recorded.replace("Z", "+00:00"))
    if recorded is not None:
        recorded = recorded.replace(tzinfo=timezone.utc) if recorded.tzinfo is None else recorded
        last_donation = max(recorded, last_donation) if last_donation else recorded
    user = auth_repo.update_user(user_id, display_name=payload.display_name)
    region_id = auth_repo.upsert_user_region_preference(user_id, region_id)

    profile_data = payload.model_dump(mode="json")
    profile_data["city"] = city
    profile_data["region_id"] = region_id

    existing = auth_repo.get_donor_profile(user_id) or {}
    profile_data["eligibility_status"] = existing.get("eligibility_status", "eligible")
    profile_data["last_donation_at"] = last_donation
    profile_data["next_eligible_at"] = (
        _next_eligible_at(last_donation) if last_donation is not None
        else existing.get("next_eligible_at") or _next_eligible_at(None)
    )
    profile_data["donation_history"] = existing.get("donation_history", [])
    profile = auth_repo.upsert_donor_profile(user_id, profile_data)
    response_profile = {**profile, "region_id": region_id}
    return DonorProfileResponse(**response_profile, display_name=user.display_name, phone=user.phone)


@router.post("/donor-profile/donations/complete", response_model=DonorProfileResponse)
async def complete_donor_donation(payload: DonorDonationCompleteRequest, identity: Identity = Depends(get_identity)):
    """Record a completed donation and apply the server-controlled deferral window."""
    require_role(identity, RoleType.DONOR)
    user_id = UUID(identity.subject_id)
    existing = auth_repo.get_donor_profile(user_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Donor onboarding is incomplete")

    donation_date = payload.donation_date or datetime.now(timezone.utc)
    if donation_date.tzinfo is None:
        donation_date = donation_date.replace(tzinfo=timezone.utc)
    if donation_date > datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Completed donation cannot be in the future")
    history = list(existing.get("donation_history") or [])
    if payload.outreach_id and any(item.get("outreach_id") == payload.outreach_id for item in history):
        user = auth_repo.get_user_by_id(user_id)
        return DonorProfileResponse(**existing, display_name=user.display_name if user else identity.display_name, phone=user.phone if user else None)
    record = {"date": donation_date.isoformat(), "status": "completed"}
    if payload.outreach_id:
        record["outreach_id"] = payload.outreach_id
    history.append(record)
    previous_date = existing.get("last_donation_at")
    if isinstance(previous_date, str):
        previous_date = datetime.fromisoformat(previous_date.replace("Z", "+00:00"))
    if previous_date is not None:
        previous_date = previous_date.replace(tzinfo=timezone.utc) if previous_date.tzinfo is None else previous_date
        donation_date = max(previous_date, donation_date)
    updated = {
        **existing,
        "eligibility_status": "deferred",
        "last_donation_at": donation_date,
        "next_eligible_at": _six_months_after(donation_date),
        "donation_history": history,
    }
    profile = auth_repo.upsert_donor_profile(user_id, updated)
    user = auth_repo.get_user_by_id(user_id)
    return DonorProfileResponse(**profile, display_name=user.display_name if user else identity.display_name, phone=user.phone if user else None)
