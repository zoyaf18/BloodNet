"""
Core domain entities for BloodNet.

These mirror SPEC.md §3.1 exactly. They are the shared contract every service
imports from — match-svc, swarm-svc, inventory branch, agent-svc tools, and the
digital twin should all speak these types rather than inventing their own.

Kept dependency-light (pydantic only) so it can be vendored into Cloud Run
services and Vertex Pipelines alike without pulling in a service-specific stack.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class BloodGroup(str, Enum):
    O_NEG = "O-"
    O_POS = "O+"
    A_NEG = "A-"
    A_POS = "A+"
    B_NEG = "B-"
    B_POS = "B+"
    AB_NEG = "AB-"
    AB_POS = "AB+"


class Component(str, Enum):
    RBC = "RBC"
    WHOLE_BLOOD = "Whole Blood"
    PLATELETS_RDP = "Platelets (RDP)"
    PLATELETS_SDP = "Platelets (SDP)"
    FFP = "FFP"
    CRYOPRECIPITATE = "Cryoprecipitate"


class Urgency(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    ROUTINE = "Routine"


# Shelf life in days per component — drives expiry-risk and inventory filtering.
# Platelets 5d, RBC 35-42d (use 42 as the ceiling), FFP 1y frozen.
SHELF_LIFE_DAYS: dict[Component, int] = {
    Component.RBC: 42,
    Component.WHOLE_BLOOD: 35,
    Component.PLATELETS_RDP: 5,
    Component.PLATELETS_SDP: 5,
    Component.FFP: 365,
    Component.CRYOPRECIPITATE: 365,
}


class InventoryStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ISSUED = "issued"
    DISCARDED = "discarded"
    IN_TRANSIT = "in_transit"


class GeoPoint(BaseModel):
    lat: float
    lng: float


class UserLocation(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class Donor(BaseModel):
    donor_id: str
    blood_group: BloodGroup
    geo: GeoPoint
    age_years: int | None = None
    contact_tokens: list[str] = Field(default_factory=list)  # C-02: tokens, not raw phone numbers
    last_donation_at: datetime | None = None
    deferral_flags: list["DeferralFlag"] = Field(default_factory=list)
    consent_scopes: list[str] = Field(default_factory=list)  # e.g. "contactable", "analytics", "regional_sharing"
    reliability_features: dict[str, float] = Field(default_factory=dict)


class DeferralFlag(BaseModel):
    reason: str
    deferred_until: date | None = None  # None => permanent deferral


class BloodBank(BaseModel):
    bank_id: str
    name: str
    geo: GeoPoint
    licence_id: str
    storage_capacity: dict[str, int] = Field(default_factory=dict)  # component -> units
    min_reserve: dict[str, int] = Field(default_factory=dict)  # "group|component" -> units
    operating_hours: str = "24x7"


class Hospital(BaseModel):
    hospital_id: str
    name: str
    geo: GeoPoint
    tier: str
    affiliated_banks: list[str] = Field(default_factory=list)
    demand_profile: dict[str, float] = Field(default_factory=dict)


class InventoryUnit(BaseModel):
    unit_id: str
    bank_id: str
    group: BloodGroup
    component: Component
    collected_at: datetime
    expires_at: datetime
    status: InventoryStatus = InventoryStatus.AVAILABLE


class VerificationState(str, Enum):
    UNVERIFIED = "unverified"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"


class RequestStatus(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    FULFILLED = "fulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    UNFULFILLED = "unfulfilled"
    CANCELLED = "cancelled"


class Request(BaseModel):
    request_id: str
    group: BloodGroup
    component: Component
    qty: int
    hospital_id: str
    urgency: Urgency
    required_by: datetime
    source_channel: str
    # A case belongs to the hospital's configured operational region.  This is
    # deliberately a request attribute so downstream projections, forecasts,
    # and authorization decisions share one scope.
    region: str | None = None
    verification_state: VerificationState = VerificationState.UNVERIFIED
    status: RequestStatus = RequestStatus.DRAFT


class InventoryMatch(BaseModel):
    """One candidate bank that can (partially) fill a request from on-hand
    stock.

    selected_unit_ids represents the read-only fulfillment plan. It does
    not mean the units have been reserved.
    """

    bank_id: str
    units_available: int
    selected_unit_ids: list[str] = Field(default_factory=list)
    distance_km: float
    eta_min: float
    reserved: bool = False

class EscalationRound(BaseModel):
    round_number: int
    radius_km: float
    cohort_size: int
    wait_seconds: int


class CaseOutcome(str, Enum):
    FULFILLED = "fulfilled"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    UNFULFILLED = "unfulfilled"
    CANCELLED = "cancelled"
    PENDING = "pending"


class ReservationState(str, Enum):
    NOT_PROPOSED = "not_proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESERVED = "reserved"


class Case(BaseModel):
    case_id: str
    request_id: str
    fulfillment_probability: float = 0.0
    escalation_state: str = "not_started"
    rounds: list[EscalationRound] = Field(default_factory=list)
    outcome: CaseOutcome = CaseOutcome.PENDING

    # v1.1 additions — inventory-aware matching (SPEC §3.1, §6.1)
    inventory_matches: list[InventoryMatch] = Field(default_factory=list)
    units_from_inventory: int = 0
    units_from_donors_remaining: int = 0
    donor_target_units: int = 0
    units_from_donors_fulfilled: int = 0
    confirmed_inventory_units: int = Field(default=0, ge=0)
    confirmed_donor_units: int = Field(default=0, ge=0)
    outcome_confirmations: dict[str, dict] = Field(default_factory=dict)
    reservation_state: ReservationState = ReservationState.NOT_PROPOSED
    reservation_recommendation_ids: list[str] = Field(default_factory=list)
    reservation_ids: list[str] = Field(default_factory=list)


class OutreachResponse(str, Enum):
    ACCEPT = "accept"
    DECLINE = "decline"
    NO_REPLY = "no_reply"


class Outreach(BaseModel):
    outreach_id: str
    case_id: str
    donor_id: str
    round: int
    sent_at: datetime
    responded_at: datetime | None = None
    response: OutreachResponse | None = None
    completed: bool = False


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(BaseModel):
    notification_id: str
    case_id: str
    request_id: str
    donor_id: str
    recipient_type: str = "donor"
    recipient_id: str | None = None
    channel: str = "demo"
    message: str
    status: NotificationStatus = NotificationStatus.PENDING
    created_at: datetime
    sent_at: datetime | None = None
    provider_message_id: str | None = None
    delivery_status: str = "pending"
    delivery_failure_reason: str | None = None
    delivered_at: datetime | None = None


class AuditRecord(BaseModel):
    audit_id: str
    action: str
    request_id: str
    case_id: str
    event_id: str | None = None
    actor: str | None = None
    details: dict = Field(default_factory=dict)
    prev_audit_hash: str | None = None
    at: datetime

    @property
    def record_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"record_hash"}, exclude_none=True)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Transfer(BaseModel):
    transfer_id: str
    from_bank: str
    to_bank: str
    units: list[str]  # unit_ids
    reason: str
    case_id: str | None = None
    status: str = "in_transit"
    received_at: datetime | None = None


# ============================================================================
# AUTHENTICATION & AUTHORIZATION MODELS
# ============================================================================
# These models support the authentication system:
# - User registration, login, email verification, password reset
# - Organization onboarding and multi-tenancy
# - Role-based access control (RBAC)
# - Invitation workflows


class UserStatus(str, Enum):
    """User account lifecycle states per spec §6."""
    REGISTERED = "registered"
    EMAIL_UNVERIFIED = "email_unverified"
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


class RoleType(str, Enum):
    """User roles within an organization per spec §1."""
    DONOR = "donor"
    HOSPITAL_COORDINATOR = "hospital_coordinator"
    BANK_ADMIN = "bank_admin"
    REGIONAL_ADMIN = "regional_admin"
    AUDITOR = "auditor"


class OrganizationType(str, Enum):
    """Organization types for multi-tenancy per spec §10."""
    HOSPITAL = "hospital"
    BLOOD_BANK = "blood_bank"
    REGIONAL = "regional"
    PLATFORM = "platform"


class MembershipStatus(str, Enum):
    """Membership lifecycle states per spec §6."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    INVITED = "invited"


class InvitationStatus(str, Enum):
    """Invitation lifecycle states per spec §11."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REJECTED = "rejected"


class AuditEventType(str, Enum):
    """Authentication and authorization audit events per spec §6."""
    USER_REGISTERED = "user_registered"
    EMAIL_VERIFICATION_SENT = "email_verification_sent"
    EMAIL_VERIFIED = "email_verified"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    ORGANIZATION_CREATED = "organization_created"
    ORGANIZATION_VERIFIED = "organization_verified"
    ORGANIZATION_SUSPENDED = "organization_suspended"
    INVITATION_SENT = "invitation_sent"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_EXPIRED = "invitation_expired"
    MEMBERSHIP_ACTIVATED = "membership_activated"
    MEMBERSHIP_SUSPENDED = "membership_suspended"
    MEMBERSHIP_REVOKED = "membership_revoked"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    MFA_RESET = "mfa_reset"


class User(BaseModel):
    """Persistent user account per spec §5."""
    id: str  # UUID
    identity_subject: str | None = None  # External IdP subject
    email: str
    email_verified: bool = False
    email_verified_at: datetime | None = None
    display_name: str | None = None
    phone: str | None = None
    password_hash: str | None = None  # bcrypt hash
    password_reset_token: str | None = None
    password_reset_expires_at: datetime | None = None
    mfa_enabled: bool = False
    mfa_secret: str | None = None  # encrypted
    status: UserStatus = UserStatus.REGISTERED
    created_at: datetime
    updated_at: datetime
    created_by: str | None = None  # FK to User.id
    updated_by: str | None = None  # FK to User.id


class Organization(BaseModel):
    """Organization (hospital, blood bank, regional) per spec §10."""
    id: str  # UUID
    name: str
    type: OrganizationType
    address: str | None = None
    contact_email: str
    contact_phone: str | None = None
    metadata: dict = Field(default_factory=dict)
    verified: bool = False
    verified_at: datetime | None = None
    verified_by: str | None = None  # FK to User.id
    status: str = "pending"  # pending, active, suspended
    created_at: datetime
    updated_at: datetime


class OrganizationMembership(BaseModel):
    """User-organization relationship with role per spec §13."""
    id: str  # UUID
    user_id: str  # FK
    organization_id: str  # FK
    role: RoleType
    status: MembershipStatus = MembershipStatus.ACTIVE
    invited_by: str | None = None  # FK to User.id
    approved_by: str | None = None  # FK to User.id
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class Invitation(BaseModel):
    """Pre-membership invitation per spec §11."""
    id: str  # UUID
    organization_id: str  # FK
    email: str
    role: RoleType
    token_hash: str  # SHA256 hash of the secret token
    status: InvitationStatus = InvitationStatus.PENDING
    invited_by: str  # FK to User.id
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by: str | None = None  # FK to User.id
    created_at: datetime


class AuditEvent(BaseModel):
    """Audit trail for authentication and authorization events per spec §6."""
    id: str  # UUID
    actor_id: str | None = None  # FK to User.id
    event_type: AuditEventType
    resource_type: str  # user, organization, membership, invitation
    resource_id: str  # UUID of the resource
    changes: dict | None = None  # {before, after} for important fields
    status: str = "success"  # success, failure
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime


# ============================================================================
# API REQUEST/RESPONSE SCHEMAS
# ============================================================================


class UserRegisterRequest(BaseModel):
    """Self-registration request per spec §2."""
    email: str
    password: str
    password_confirm: str
    display_name: str
    phone: str | None = None
    role: RoleType = RoleType.DONOR


class RoleAccessRequest(BaseModel):
    role: RoleType
    organization_id: str | None = None
    organization_name: str | None = None
    organization_type: OrganizationType | None = None
    organization_address: str | None = None
    notes: str | None = None


class UserLocationRequest(UserLocation):
    """Current browser/device location used by proximity-based workflows."""


class UserRegionPreferenceRequest(BaseModel):
    """A user's personal service-region preference; it does not grant data access."""
    region_id: str = Field(min_length=2, max_length=80)


class DonorProfileRequest(BaseModel):
    """Profile fields collected after verified donor phone sign-in."""
    display_name: str
    blood_group: BloodGroup
    date_of_birth: date | None = None
    city: str = Field(min_length=1)
    region_id: str = Field(min_length=1, max_length=80)
    availability: str = "available"
    consent_contact: bool = False
    notification_channels: list[str] = Field(default_factory=lambda: ["email"])
    eligibility_status: str = "eligible"
    last_donation_at: datetime | None = None
    next_eligible_at: datetime | None = None
    donation_history: list[dict] = Field(default_factory=list)


class DonorDonationCompleteRequest(BaseModel):
    donation_date: datetime | None = None
    outreach_id: str | None = None


class DonorProfileResponse(DonorProfileRequest):
    user_id: str
    phone: str | None = None


class UserLoginRequest(BaseModel):
    """Login request per spec §4."""
    email: str
    password: str


class UserProfileResponse(BaseModel):
    """User profile response returned by /me endpoint per spec §5."""
    id: str
    subject_id: str | None = None
    email: str
    display_name: str | None = None
    phone: str | None = None
    status: UserStatus
    role: RoleType
    organization: dict | None = None  # {id, name, type}
    bank_id: str | None = None
    hospital_id: str | None = None
    region_id: str | None = None
    location: UserLocation | None = None
    permissions: list[str] = Field(default_factory=list)
    mfa_enabled: bool = False
    created_at: datetime


class MfaEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MfaVerifyRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    """JWT token response per spec §4."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserProfileResponse


class PasswordResetRequest(BaseModel):
    """Password reset initiation per spec §8."""
    email: str


class PasswordResetConfirm(BaseModel):
    """Password reset completion per spec §8."""
    token: str
    password: str
    password_confirm: str


class InvitationAcceptRequest(BaseModel):
    """Accept an invitation per spec §11."""
    token: str
    password: str
    password_confirm: str
    display_name: str | None = None


class InvitationCreateRequest(BaseModel):
    """Create an organization invitation per spec §11."""
    organization_id: str
    email: str
    role: RoleType


class OrganizationCreateRequest(BaseModel):
    """Organization onboarding request per spec §10."""
    name: str
    type: OrganizationType
    contact_email: str
    contact_phone: str | None = None
    address: str | None = None
    metadata: dict = Field(default_factory=dict)
    owner_email: str | None = None
    owner_name: str | None = None
    owner_password: str | None = None
    owner_password_confirm: str | None = None


class OrganizationUpdateRequest(BaseModel):
    """Update organization profile fields for the current authenticated organization."""
    name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address: str | None = None
    region_id: str | None = None
    registration_id: str | None = None
    metadata: dict | None = None
    facility_location: UserLocation | None = None


class OrganizationRegionUpdateRequest(BaseModel):
    """Operational region for a hospital, blood bank, or regional organization."""
    region_id: str = Field(min_length=2, max_length=80)


class Identity(BaseModel):
    """Resolved identity from JWT token per spec §4."""
    subject_id: str  # User.id
    email: str
    display_name: str
    role: RoleType
    organization_id: str | None = None
    permissions: list[str] = Field(default_factory=list)
    bank_id: str | None = None  # Derived if org.type == BLOOD_BANK
    hospital_id: str | None = None  # Derived if org.type == HOSPITAL
    region_id: str | None = None  # Derived if org.type == REGIONAL
    location: UserLocation | None = None

    def __init__(self, subject_id: str | None = None, email: str | None = None, role: RoleType | str | None = None, **data):
        if role is None and email in {item.value for item in RoleType}:
            role, email = email, ""
        if "region" in data and "region_id" not in data:
            data["region_id"] = data.pop("region")
        if subject_id is not None:
            data["subject_id"] = subject_id
        if email is not None:
            data["email"] = email
        if role is not None:
            data["role"] = role
        data.setdefault("display_name", data.get("email") or "User")
        super().__init__(**data)
    
    def has_permission(self, perm: str) -> bool:
        """Check if identity has a specific permission."""
        return perm in self.permissions
    
    def has_role(self, *roles: str) -> bool:
        """Check if identity has any of the given roles."""
        return self.role.value in roles if isinstance(self.role, RoleType) else self.role in roles
    approval_id: str | None = None


class InventoryReservationProposal(BaseModel):
    case_id: str
    request_id: str
    bank_id: str
    unit_ids: list[str] = Field(default_factory=list)
    reservation_id: str
    expires_at: datetime | None = None


class Recommendation(BaseModel):
    rec_id: str
    type: str
    payload: dict
    # Operational recommendations are region-bound. This remains optional at
    # validation time so legacy rows can be loaded and quarantined by the API
    # instead of making the whole recommendation store unreadable.
    region_id: str | None = None
    request_id: str | None = None
    case_id: str | None = None
    rationale: str = ""
    evidence: list[dict] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    expected_impact: dict = Field(default_factory=dict)
    options: list[dict] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    state: str = "AWAITING_APPROVAL"
    reservation_proposal: InventoryReservationProposal | None = None


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class Approval(BaseModel):
    approval_id: str
    rec_id: str
    actor: str
    decision: ApprovalDecision
    rationale: str
    at: datetime


class ExecutionStatus(str, Enum):
    EXECUTED = "executed"
    FAILED = "failed"
    PARTIAL = "partial"
    ESCALATED = "escalated"


class ExecutionRecord(BaseModel):
    execution_id: str
    recommendation_id: str
    approval_id: str
    request_id: str
    case_id: str
    status: str
    result: dict = Field(default_factory=dict)
    failure_reason: str | None = None
    rollback_status: str | None = None
    created_at: datetime
    updated_at: datetime


class InventoryReservationStatus(str, Enum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"
    EXPIRED = "expired"


class InventoryReservation(BaseModel):
    reservation_id: str
    case_id: str
    request_id: str
    bank_id: str
    unit_ids: list[str] = Field(default_factory=list)
    status: InventoryReservationStatus = InventoryReservationStatus.RESERVED
    created_at: datetime
    expires_at: datetime | None = None
    consumed_at: datetime | None = None
    released_at: datetime | None = None
