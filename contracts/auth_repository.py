"""
PostgreSQL repository for authentication and authorization.

Handles user accounts, organizations, memberships, invitations, and audit events.
Uses psycopg directly following the existing pattern in the codebase.

Spec reference: Auth & Registration Spec §5.2, §10, §11
"""

import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from contracts.models import (
    AuditEvent,
    AuditEventType,
    Identity,
    Invitation,
    InvitationStatus,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationType,
    RoleType,
    UserLocation,
    User,
    UserStatus,
)


class AuthRepository:
    """Repository for authentication and authorization operations."""

    def __init__(self, database_url: str):
        """Initialize with PostgreSQL connection string."""
        self._database_url = database_url

    @contextmanager
    def _connection(self) -> Iterator[psycopg.Connection]:
        """Context manager for database connection with auto-commit/rollback."""
        with psycopg.connect(self._database_url, row_factory=dict_row) as conn:
            yield conn
            conn.commit()

    # =========================================================================
    # USER OPERATIONS
    # =========================================================================

    def create_user(
        self,
        email: str,
        display_name: str | None = None,
        phone: str | None = None,
        password_hash: str | None = None,
        identity_subject: str | None = None,
        created_by: UUID | None = None,
    ) -> User:
        """Create a new user account."""
        user_id = uuid4()
        
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                        INSERT INTO users (
                            id, email, display_name, phone, password_hash,
                            identity_subject, status, created_at, updated_at, created_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                    """),
                    (
                        str(user_id),
                        email.lower(),
                        display_name,
                        phone,
                        password_hash,
                        identity_subject,
                        UserStatus.EMAIL_UNVERIFIED.value,
                        datetime.now(timezone.utc),
                        datetime.now(timezone.utc),
                        str(created_by) if created_by else None,
                    ),
                )
                row = cur.fetchone()
                
                # Audit log
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.USER_REGISTERED,
                    resource_type="user",
                    resource_id=str(user_id),
                    actor_id=created_by,
                    changes={"email": email, "display_name": display_name},
                    status="success",
                )
                
                return self._row_to_user(row)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Retrieve a user by email (case-insensitive)."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM users WHERE LOWER(email) = %s",
                    (email.lower(),),
                )
                row = cur.fetchone()
                return self._row_to_user(row) if row else None

    def get_user_by_id(self, user_id: UUID) -> Optional[User]:
        """Retrieve a user by ID."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE id = %s", (str(user_id),))
                row = cur.fetchone()
                return self._row_to_user(row) if row else None

    def get_user_by_identity_subject(self, subject: str) -> Optional[User]:
        """Retrieve a user by external identity subject."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM users WHERE identity_subject = %s",
                    (subject,),
                )
                row = cur.fetchone()
                return self._row_to_user(row) if row else None

    def get_user_location(self, user_id: UUID) -> UserLocation | None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT lat, lng, accuracy_m FROM user_locations WHERE user_id = %s", (str(user_id),))
                row = cur.fetchone()
                return UserLocation(**dict(row)) if row else None

    def upsert_user_location(self, user_id: UUID, location: UserLocation) -> UserLocation:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_locations (user_id, lat, lng, accuracy_m, updated_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        lat = EXCLUDED.lat, lng = EXCLUDED.lng,
                        accuracy_m = EXCLUDED.accuracy_m, updated_at = now()
                    RETURNING lat, lng, accuracy_m
                    """,
                    (str(user_id), location.lat, location.lng, location.accuracy_m),
                )
                return UserLocation(**dict(cur.fetchone()))

    def get_user_region_preference(self, user_id: UUID) -> str | None:
        """Return a personal region preference without changing authorization scope."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT region_id FROM user_region_preferences WHERE user_id = %s",
                    (str(user_id),),
                )
                row = cur.fetchone()
                return str(row["region_id"]) if row else None

    def upsert_user_region_preference(self, user_id: UUID, region_id: str) -> str:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_region_preferences (user_id, region_id, updated_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        region_id = EXCLUDED.region_id,
                        updated_at = now()
                    RETURNING region_id
                    """,
                    (str(user_id), region_id),
                )
                return str(cur.fetchone()["region_id"])

    def update_user(
        self,
        user_id: UUID,
        display_name: str | None = None,
        phone: str | None = None,
        password_hash: str | None = None,
        identity_subject: str | None = None,
        status: UserStatus | None = None,
        email_verified: bool | None = None,
        updated_by: UUID | None = None,
        mfa_enabled: bool | None = None,
        mfa_secret: str | None = None,
    ) -> User:
        """Update user fields."""
        updates = {}
        if display_name is not None:
            updates["display_name"] = display_name
        if phone is not None:
            updates["phone"] = phone
        if password_hash is not None:
            updates["password_hash"] = password_hash
        if identity_subject is not None:
            updates["identity_subject"] = identity_subject
        if status is not None:
            updates["status"] = status.value
        if email_verified is not None:
            updates["email_verified"] = email_verified
            if email_verified:
                updates["email_verified_at"] = datetime.now(timezone.utc)
        if mfa_enabled is not None:
            updates["mfa_enabled"] = mfa_enabled
        if mfa_secret is not None:
            updates["mfa_secret"] = mfa_secret

        updates["updated_at"] = datetime.now(timezone.utc)
        if updated_by:
            updates["updated_by"] = str(updated_by)

        set_clause = ", ".join([f"{k} = %s" for k in updates.keys()])
        values = list(updates.values()) + [str(user_id)]

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL(f"""
                        UPDATE users SET {set_clause}
                        WHERE id = %s
                        RETURNING *
                    """),
                    values,
                )
                row = cur.fetchone()
                return self._row_to_user(row)

    def upsert_donor_profile(self, user_id: UUID, profile: dict) -> dict:
        """Persist the donor onboarding profile and contact preference."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO donor_profiles
                        (user_id, blood_group, date_of_birth, city, availability, consent_contact, notification_channels, eligibility_status, last_donation_at, next_eligible_at, donation_history, consent_updated_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
                    ON CONFLICT (user_id) DO UPDATE SET
                        blood_group = EXCLUDED.blood_group,
                        date_of_birth = EXCLUDED.date_of_birth,
                        city = EXCLUDED.city,
                        availability = EXCLUDED.availability,
                        consent_contact = EXCLUDED.consent_contact,
                        notification_channels = EXCLUDED.notification_channels,
                        eligibility_status = EXCLUDED.eligibility_status,
                        last_donation_at = EXCLUDED.last_donation_at,
                        next_eligible_at = EXCLUDED.next_eligible_at,
                        donation_history = EXCLUDED.donation_history,
                        consent_updated_at = now(),
                        updated_at = now()
                    RETURNING user_id, blood_group, date_of_birth, city, availability, consent_contact, notification_channels, eligibility_status, last_donation_at, next_eligible_at, donation_history
                    """,
                    (str(user_id), profile["blood_group"], profile.get("date_of_birth"), profile.get("city"), profile["availability"], profile["consent_contact"], Jsonb(profile["notification_channels"]), profile["eligibility_status"], profile.get("last_donation_at"), profile.get("next_eligible_at"), Jsonb(profile["donation_history"])),
                )
                result = dict(cur.fetchone())
                result["user_id"] = str(result["user_id"])
                return result

    def get_donor_profile(self, user_id: UUID) -> dict | None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, blood_group, date_of_birth, city, availability, consent_contact, notification_channels, eligibility_status, last_donation_at, next_eligible_at, donation_history FROM donor_profiles WHERE user_id = %s", (str(user_id),))
                row = cur.fetchone()
                if not row:
                    return None
                result = dict(row)
                result["user_id"] = str(result["user_id"])
                return result

    def list_contactable_donor_profiles(self) -> list[dict]:
        """Return donor profiles that have opted into compatible outreach."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT donor_profiles.user_id, blood_group, date_of_birth, availability,
                           consent_contact, notification_channels, eligibility_status,
                           next_eligible_at, last_donation_at, user_locations.lat, user_locations.lng,
                           user_region_preferences.region_id
                    FROM donor_profiles
                    INNER JOIN user_locations ON user_locations.user_id = donor_profiles.user_id
                    INNER JOIN users ON users.id = donor_profiles.user_id
                    LEFT JOIN user_region_preferences ON user_region_preferences.user_id = donor_profiles.user_id
                    WHERE consent_contact = TRUE
                      AND availability = 'available'
                      AND eligibility_status = 'eligible'
                      AND users.status = 'active'
                      AND users.email_verified = TRUE
                      AND date_of_birth <= CURRENT_DATE - INTERVAL '18 years'
                      AND (next_eligible_at IS NULL OR next_eligible_at <= NOW())
                    """
                )
                return [
                    {**dict(row), "user_id": str(row["user_id"])}
                    for row in cur.fetchall()
                ]

    def set_password_reset_token(self, user_id: UUID, token_hash: str, expires_at: datetime) -> None:
        """Store a password reset token."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        UPDATE users
                        SET password_reset_token = %s, password_reset_expires_at = %s
                        WHERE id = %s
                    """,
                    (token_hash, expires_at, str(user_id)),
                )
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.PASSWORD_RESET_REQUESTED,
                    resource_type="user",
                    resource_id=str(user_id),
                    actor_id=None,
                    status="success",
                )

    def verify_password_reset_token(self, user_id: UUID, token_hash: str) -> bool:
        """Verify a password reset token is valid and not expired."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT password_reset_token, password_reset_expires_at
                        FROM users
                        WHERE id = %s
                    """,
                    (str(user_id),),
                )
                row = cur.fetchone()
                if not row:
                    return False
                
                stored_token = row["password_reset_token"]
                expires_at = row["password_reset_expires_at"]
                
                return (
                    stored_token == token_hash
                    and expires_at is not None
                    and expires_at > datetime.now(timezone.utc)
                )

    def clear_password_reset_token(self, user_id: UUID) -> None:
        """Clear password reset token after use."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        UPDATE users
                        SET password_reset_token = NULL, password_reset_expires_at = NULL
                        WHERE id = %s
                    """,
                    (str(user_id),),
                )

    # =========================================================================
    # ORGANIZATION OPERATIONS
    # =========================================================================

    def create_organization(
        self,
        name: str,
        org_type: OrganizationType,
        contact_email: str,
        contact_phone: str | None = None,
        address: str | None = None,
        metadata: dict | None = None,
    ) -> Organization:
        """Create a new organization."""
        org_id = uuid4()
        metadata = dict(metadata or {})
        if org_type == OrganizationType.BLOOD_BANK and not metadata.get("bank_id"):
            metadata["bank_id"] = "BANK-001"

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                        INSERT INTO organizations (
                            id, name, type, contact_email, contact_phone, address,
                            metadata, status, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                    """),
                    (
                        str(org_id),
                        name,
                        org_type.value,
                        contact_email,
                        contact_phone,
                        address,
                        Jsonb(metadata),
                        "pending",
                        datetime.now(timezone.utc),
                        datetime.now(timezone.utc),
                    ),
                )
                row = cur.fetchone()
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.ORGANIZATION_CREATED,
                    resource_type="organization",
                    resource_id=str(org_id),
                    actor_id=None,
                    changes={"name": name, "type": org_type.value},
                    status="success",
                )
                
                return self._row_to_organization(row)

    def get_organization_by_id(self, org_id: UUID) -> Optional[Organization]:
        """Retrieve an organization by ID."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM organizations WHERE id = %s", (str(org_id),))
                row = cur.fetchone()
                return self._row_to_organization(row) if row else None

    def get_organization_by_type(self, org_type: OrganizationType) -> Optional[Organization]:
        """Find the first organization of a given type."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM organizations WHERE type = %s AND status = 'active' ORDER BY created_at ASC LIMIT 1",
                    (org_type.value,),
                )
                row = cur.fetchone()
                return self._row_to_organization(row) if row else None

    def list_organizations(self, status: str | None = None) -> list[Organization]:
        """List organizations for platform administration."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute("SELECT * FROM organizations WHERE status = %s ORDER BY created_at DESC", (status,))
                else:
                    cur.execute("SELECT * FROM organizations ORDER BY created_at DESC")
                return [self._row_to_organization(row) for row in cur.fetchall()]

    def list_memberships(self, organization_id: UUID | None = None, status: MembershipStatus | None = None) -> list[OrganizationMembership]:
        """List organization memberships for platform administration."""
        clauses = []
        values: list[str] = []
        if organization_id:
            clauses.append("organization_id = %s")
            values.append(str(organization_id))
        if status:
            clauses.append("status = %s")
            values.append(status.value)
        query = "SELECT * FROM organization_memberships"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
                return [self._row_to_membership(row) for row in cur.fetchall()]

    def verify_organization(self, org_id: UUID, verified_by: UUID) -> Organization:
        """Verify an organization (admin action)."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)
                cur.execute(
                    """
                        UPDATE organizations
                        SET verified = true, verified_at = %s, verified_by = %s, status = %s
                        WHERE id = %s
                        RETURNING *
                    """,
                    (now, str(verified_by), "active", str(org_id)),
                )
                row = cur.fetchone()
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.ORGANIZATION_VERIFIED,
                    resource_type="organization",
                    resource_id=str(org_id),
                    actor_id=verified_by,
                    status="success",
                )
                
                return self._row_to_organization(row)

    def update_organization(
        self,
        organization_id: UUID,
        status: str | None = None,
        metadata: dict | None = None,
        name: str | None = None,
        address: str | None = None,
        contact_email: str | None = None,
        contact_phone: str | None = None,
    ) -> Organization:
        """Update organization fields."""
        updates = {}
        if status is not None:
            updates["status"] = status
        if name is not None:
            updates["name"] = name
        if address is not None:
            updates["address"] = address
        if contact_email is not None:
            updates["contact_email"] = contact_email
        if contact_phone is not None:
            updates["contact_phone"] = contact_phone
        if metadata is not None:
            updates["metadata"] = Jsonb(metadata)

        if not updates:
            return self.get_organization_by_id(organization_id) or None

        updates["updated_at"] = datetime.now(timezone.utc)
        set_clause = ", ".join([f"{k} = %s" for k in updates.keys()])
        values = list(updates.values()) + [str(organization_id)]

        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                        UPDATE organizations
                        SET {set_clause}
                        WHERE id = %s
                        RETURNING *
                    """,
                    values,
                )
                row = cur.fetchone()
                return self._row_to_organization(row)

    # =========================================================================
    # ORGANIZATION MEMBERSHIP OPERATIONS
    # =========================================================================

    def create_membership(
        self,
        user_id: UUID,
        organization_id: UUID,
        role: RoleType,
        invited_by: UUID | None = None,
        status: MembershipStatus = MembershipStatus.ACTIVE,
        metadata: dict | None = None,
    ) -> OrganizationMembership:
        """Create a user-organization membership."""
        membership_id = uuid4()
        
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                        INSERT INTO organization_memberships (
                            id, user_id, organization_id, role, status,
                            invited_by, created_at, updated_at, metadata
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                    """),
                    (
                        str(membership_id),
                        str(user_id),
                        str(organization_id),
                        role.value,
                        status.value,
                        str(invited_by) if invited_by else None,
                        datetime.now(timezone.utc),
                        datetime.now(timezone.utc),
                        Jsonb(metadata or {}),
                    ),
                    )
                row = cur.fetchone()
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.MEMBERSHIP_ACTIVATED if status == MembershipStatus.ACTIVE else AuditEventType.INVITATION_SENT,
                    resource_type="membership",
                    resource_id=str(membership_id),
                    actor_id=invited_by,
                    changes={"role": role.value, "status": status.value},
                    status="success",
                )
                
                return self._row_to_membership(row)

    def get_membership_by_user_org(self, user_id: UUID, org_id: UUID) -> Optional[OrganizationMembership]:
        """Retrieve a membership by user and organization."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM organization_memberships
                        WHERE user_id = %s AND organization_id = %s
                    """,
                    (str(user_id), str(org_id)),
                )
                row = cur.fetchone()
                return self._row_to_membership(row) if row else None

    def get_primary_membership(self, user_id: UUID) -> Optional[OrganizationMembership]:
        """Resolve the active operational membership for a user.

        Every account begins as a donor. Once an elevated membership is approved,
        it must take precedence over that base donor membership; otherwise a user
        can be approved successfully yet continue to receive donor permissions.
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM organization_memberships
                        WHERE user_id = %s AND status = 'active'
                        ORDER BY (role <> 'donor') DESC, created_at DESC
                        LIMIT 1
                    """,
                    (str(user_id),),
                )
                row = cur.fetchone()
                return self._row_to_membership(row) if row else None

    def update_membership_status(
        self, membership_id: UUID, status: MembershipStatus, approved_by: UUID | None = None
    ) -> OrganizationMembership:
        """Change membership state during organization approval."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organization_memberships
                    SET status = %s, approved_by = %s, approved_at = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (status.value, str(approved_by) if approved_by else None,
                     datetime.now(timezone.utc) if approved_by else None, str(membership_id)),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Membership not found")
                return self._row_to_membership(row)

    def update_membership_role(self, membership_id: UUID, role: RoleType, updated_by: UUID) -> OrganizationMembership:
        """Change a membership role with an audit record."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE organization_memberships SET role = %s, updated_at = %s WHERE id = %s RETURNING *",
                    (role.value, datetime.now(timezone.utc), str(membership_id)),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Membership not found")
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.MEMBERSHIP_ACTIVATED,
                    resource_type="membership",
                    resource_id=str(membership_id),
                    actor_id=updated_by,
                    changes={"role": role.value},
                    status="success",
                )
                return self._row_to_membership(row)

    def get_memberships_by_user(self, user_id: UUID) -> list[OrganizationMembership]:
        """Get all memberships for a user."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM organization_memberships
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                    """,
                    (str(user_id),),
                )
                rows = cur.fetchall()
                return [self._row_to_membership(row) for row in rows]

    def create_role_access_request(self, user_id: UUID, organization_id: UUID, role: RoleType, notes: str | None = None) -> dict:
        """Create a pending elevated-role request."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO role_access_requests (user_id, organization_id, requested_role, notes)
                       VALUES (%s, %s, %s, %s) RETURNING *""",
                    (str(user_id), str(organization_id), role.value, notes),
                )
                return dict(cur.fetchone())

    def list_role_access_requests(self, status: str = "pending") -> list[dict]:
        """List role requests for the approval queue."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM role_access_requests WHERE status = %s ORDER BY created_at DESC", (status,))
                return [dict(row) for row in cur.fetchall()]

    def list_public_donor_counts(self) -> list[dict]:
        """Return eligible donor totals grouped by their operational region."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(
                        (
                            SELECT membership.metadata->>'region'
                            FROM organization_memberships AS membership
                            WHERE membership.user_id = profile.user_id
                              AND membership.status = 'active'
                              AND membership.metadata->>'region' IS NOT NULL
                            ORDER BY membership.created_at
                            LIMIT 1
                        ),
                        profile.city
                    ) AS region,
                    profile.city AS city,
                    COUNT(*)::int AS count
                    FROM donor_profiles AS profile
                    WHERE profile.availability = 'available'
                      AND profile.eligibility_status = 'eligible'
                      AND profile.consent_contact = true
                    GROUP BY 1, 2
                    """
                )
                return [dict(row) for row in cur.fetchall()]

    def get_role_access_request(self, request_id: UUID) -> dict | None:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM role_access_requests WHERE id = %s", (str(request_id),))
                row = cur.fetchone()
                return dict(row) if row else None

    def update_role_access_request(self, request_id: UUID, status: str, reviewed_by: UUID) -> dict:
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE role_access_requests SET status = %s, reviewed_by = %s, reviewed_at = %s
                       WHERE id = %s RETURNING *""",
                    (status, str(reviewed_by), datetime.now(timezone.utc), str(request_id)),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Role access request not found")
                return dict(row)

    # =========================================================================
    # INVITATION OPERATIONS
    # =========================================================================

    def create_invitation(
        self,
        organization_id: UUID,
        email: str,
        role: RoleType,
        invited_by: UUID,
        expires_at: datetime,
    ) -> tuple[Invitation, str]:
        """
        Create an invitation.
        
        Returns: (invitation object, secret token)
        The token should be sent to the user; the hash is stored in DB.
        """
        invitation_id = uuid4()
        secret_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(secret_token.encode()).hexdigest()
        
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("""
                        INSERT INTO invitations (
                            id, organization_id, email, role, token_hash,
                            status, invited_by, expires_at, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                    """),
                    (
                        str(invitation_id),
                        str(organization_id),
                        email.lower(),
                        role.value,
                        token_hash,
                        InvitationStatus.PENDING.value,
                        str(invited_by),
                        expires_at,
                        datetime.now(timezone.utc),
                    ),
                )
                row = cur.fetchone()
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.INVITATION_SENT,
                    resource_type="invitation",
                    resource_id=str(invitation_id),
                    actor_id=invited_by,
                    changes={"email": email, "role": role.value},
                    status="success",
                )
                
                invitation = self._row_to_invitation(row)
                return invitation, secret_token

    def get_invitation_by_token_hash(self, token_hash: str) -> Optional[Invitation]:
        """Retrieve an invitation by token hash."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM invitations WHERE token_hash = %s",
                    (token_hash,),
                )
                row = cur.fetchone()
                return self._row_to_invitation(row) if row else None

    def accept_invitation(self, invitation_id: UUID, user_id: UUID) -> Invitation:
        """Mark an invitation as accepted."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)
                cur.execute(
                    """
                        UPDATE invitations
                        SET status = %s, accepted_at = %s, accepted_by = %s
                        WHERE id = %s
                        RETURNING *
                    """,
                    (InvitationStatus.ACCEPTED.value, now, str(user_id), str(invitation_id)),
                )
                row = cur.fetchone()
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.INVITATION_ACCEPTED,
                    resource_type="invitation",
                    resource_id=str(invitation_id),
                    actor_id=user_id,
                    status="success",
                )
                
                return self._row_to_invitation(row)

    # =========================================================================
    # AUDIT OPERATIONS
    # =========================================================================

    def get_audit_events_by_resource(
        self,
        resource_type: str,
        resource_id: UUID,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Retrieve audit events for a resource."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM audit_events
                        WHERE resource_type = %s AND resource_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """,
                    (resource_type, str(resource_id), limit),
                )
                rows = cur.fetchall()
                return [self._row_to_audit_event(row) for row in rows]

    def get_audit_events_by_actor(
        self,
        actor_id: UUID,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Retrieve audit events for an actor."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM audit_events
                        WHERE actor_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                    """,
                    (str(actor_id), limit),
                )
                rows = cur.fetchall()
                return [self._row_to_audit_event(row) for row in rows]

    # =========================================================================
    # EMAIL VERIFICATION TOKEN OPERATIONS
    # =========================================================================

    def create_email_verification_token(
        self,
        user_id: UUID,
        email: str,
        token_hash: str,
        expires_at: datetime,
    ) -> str:
        """Store an email verification token."""
        token_id = str(uuid4())
        
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        INSERT INTO email_verification_tokens (
                            id, user_id, email, token_hash, status, expires_at, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        token_id,
                        str(user_id),
                        email.lower(),
                        token_hash,
                        "pending",
                        expires_at,
                        datetime.now(timezone.utc),
                    ),
                )

    def get_email_verification_token_by_hash(self, token_hash: str) -> Optional[dict]:
        """Retrieve email verification token by hash."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM email_verification_tokens
                        WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                return cur.fetchone()

    def verify_email_token(self, token_hash: str) -> bool:
        """Mark email verification token as verified and update user."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                # Get the token
                cur.execute(
                    """
                        SELECT user_id, expires_at, status FROM email_verification_tokens
                        WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                token_row = cur.fetchone()
                
                if not token_row:
                    return False
                
                # Check not expired
                if token_row["expires_at"] < datetime.now(timezone.utc):
                    return False
                
                # Check not already verified
                if token_row["status"] != "pending":
                    return False
                
                user_id = token_row["user_id"]
                now = datetime.now(timezone.utc)
                
                # Mark token as verified
                cur.execute(
                    """
                        UPDATE email_verification_tokens
                        SET status = 'verified', verified_at = %s
                        WHERE token_hash = %s
                    """,
                    (now, token_hash),
                )
                
                # Update user status
                cur.execute(
                    """
                        UPDATE users
                        SET email_verified = true, email_verified_at = %s, status = %s
                        WHERE id = %s
                    """,
                    (now, UserStatus.ACTIVE.value, user_id),
                )
                
                # Audit
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.EMAIL_VERIFIED,
                    resource_type="user",
                    resource_id=user_id,
                    actor_id=None,
                    status="success",
                )
                
                return True

    # =========================================================================
    # PASSWORD RESET TOKEN OPERATIONS
    # =========================================================================

    def create_password_reset_token(
        self,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        """Store a password reset token."""
        token_id = str(uuid4())
        
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        INSERT INTO password_reset_tokens (
                            id, user_id, token_hash, status, expires_at, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        token_id,
                        str(user_id),
                        token_hash,
                        "pending",
                        expires_at,
                        datetime.now(timezone.utc),
                    ),
                )
                
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.PASSWORD_RESET_REQUESTED,
                    resource_type="user",
                    resource_id=str(user_id),
                    actor_id=None,
                    status="success",
                )

    def get_password_reset_token_by_hash(self, token_hash: str) -> Optional[dict]:
        """Retrieve password reset token by hash."""
        with self._connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT * FROM password_reset_tokens
                        WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                return cur.fetchone()

    def use_password_reset_token(self, token_hash: str, new_password_hash: str) -> bool:
        """
        Use a password reset token and update the user's password.
        
        Returns True if successful, False if token invalid/expired.
        """
        with self._connection() as conn:
            with conn.cursor() as cur:
                # Get the token
                cur.execute(
                    """
                        SELECT user_id, expires_at, status FROM password_reset_tokens
                        WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                token_row = cur.fetchone()
                
                if not token_row:
                    return False
                
                # Check not expired
                if token_row["expires_at"] < datetime.now(timezone.utc):
                    return False
                
                # Check not already used
                if token_row["status"] != "pending":
                    return False
                
                user_id = token_row["user_id"]
                now = datetime.now(timezone.utc)
                
                # Mark token as used
                cur.execute(
                    """
                        UPDATE password_reset_tokens
                        SET status = 'used', used_at = %s
                        WHERE token_hash = %s
                    """,
                    (now, token_hash),
                )
                
                # Invalidate all other pending reset tokens for this user
                cur.execute(
                    """
                        UPDATE password_reset_tokens
                        SET status = 'expired'
                        WHERE user_id = %s AND status = 'pending' AND token_hash != %s
                    """,
                    (user_id, token_hash),
                )
                
                # Update user password
                cur.execute(
                    """
                        UPDATE users
                        SET password_hash = %s
                        WHERE id = %s
                    """,
                    (new_password_hash, user_id),
                )
                
                # Audit
                self._log_audit_event(
                    conn,
                    event_type=AuditEventType.PASSWORD_RESET_COMPLETED,
                    resource_type="user",
                    resource_id=user_id,
                    actor_id=None,
                    status="success",
                )
                
                return True

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def log_audit_event(
        self,
        event_type: AuditEventType,
        resource_type: str,
        resource_id: str,
        actor_id: UUID | None = None,
        changes: dict | None = None,
        status: str = "success",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Persist an audit event as a standalone operation."""
        with self._connection() as conn:
            self._log_audit_event(
                conn,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                actor_id=actor_id,
                changes=changes,
                status=status,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    def _log_audit_event(
        self,
        conn: psycopg.Connection,
        event_type: AuditEventType,
        resource_type: str,
        resource_id: str,
        actor_id: UUID | None = None,
        changes: dict | None = None,
        status: str = "success",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Log an audit event (called within a transaction)."""
        event_id = str(uuid4())
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("""
                    INSERT INTO audit_events (
                        id, actor_id, event_type, resource_type, resource_id,
                        changes, status, ip_address, user_agent, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """),
                (
                    event_id,
                    str(actor_id) if actor_id else None,
                    event_type.value,
                    resource_type,
                    resource_id,
                    Jsonb(changes) if changes is not None else None,
                    status,
                    ip_address,
                    user_agent,
                    datetime.now(timezone.utc),
                ),
            )

    @staticmethod
    def _row_to_user(row: dict) -> User:
        """Convert database row to User model."""
        return User(
            id=str(row["id"]),
            identity_subject=row.get("identity_subject"),
            email=row["email"],
            email_verified=row["email_verified"],
            email_verified_at=row.get("email_verified_at"),
            display_name=row.get("display_name"),
            phone=row.get("phone"),
            password_hash=row.get("password_hash"),
            password_reset_token=row.get("password_reset_token"),
            password_reset_expires_at=row.get("password_reset_expires_at"),
            mfa_enabled=row.get("mfa_enabled", False),
            mfa_secret=row.get("mfa_secret"),
            status=UserStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            created_by=str(row["created_by"]) if row.get("created_by") else None,
            updated_by=str(row["updated_by"]) if row.get("updated_by") else None,
        )

    @staticmethod
    def _row_to_organization(row: dict) -> Organization:
        """Convert database row to Organization model."""
        return Organization(
            id=str(row["id"]),
            name=row["name"],
            type=OrganizationType(row["type"]),
            address=row.get("address"),
            contact_email=row["contact_email"],
            contact_phone=row.get("contact_phone"),
            metadata=row.get("metadata", {}),
            verified=row["verified"],
            verified_at=row.get("verified_at"),
            verified_by=str(row["verified_by"]) if row.get("verified_by") else None,
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_membership(row: dict) -> OrganizationMembership:
        """Convert database row to OrganizationMembership model."""
        return OrganizationMembership(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            organization_id=str(row["organization_id"]),
            role=RoleType(row["role"]),
            status=MembershipStatus(row["status"]),
            invited_by=str(row["invited_by"]) if row.get("invited_by") else None,
            approved_by=str(row["approved_by"]) if row.get("approved_by") else None,
            approved_at=row.get("approved_at"),
            expires_at=row.get("expires_at"),
            metadata=row.get("metadata", {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_invitation(row: dict) -> Invitation:
        """Convert database row to Invitation model."""
        return Invitation(
            id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            email=row["email"],
            role=RoleType(row["role"]),
            token_hash=row["token_hash"],
            status=InvitationStatus(row["status"]),
            invited_by=str(row["invited_by"]),
            expires_at=row["expires_at"],
            accepted_at=row.get("accepted_at"),
            accepted_by=str(row["accepted_by"]) if row.get("accepted_by") else None,
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_audit_event(row: dict) -> AuditEvent:
        """Convert database row to AuditEvent model."""
        return AuditEvent(
            id=row["id"],
            actor_id=row.get("actor_id"),
            event_type=AuditEventType(row["event_type"]),
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            changes=row.get("changes"),
            status=row["status"],
            ip_address=row.get("ip_address"),
            user_agent=row.get("user_agent"),
            created_at=row["created_at"],
        )
