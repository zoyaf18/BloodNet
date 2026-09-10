"""
Authentication utilities for BloodNet.

Handles:
- Password hashing and verification (bcrypt)
- JWT token generation and verification
- Email verification token generation
- Password reset token generation
- Token validation and expiry

Spec reference: Auth & Registration Spec §8
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from passlib.context import CryptContext

# ============================================================================
# CONFIGURATION
# ============================================================================


def _resolve_jwt_secret() -> str:
    """Return the configured signing secret, but fail closed in production.

    The service must never silently fall back to the demo development secret in
    production or identity-platform mode; that would generate valid-looking JWTs
    that fail on later verification or accept tokens signed with the wrong key.
    """
    env_secret = os.getenv("BLOODNET_JWT_SECRET") or os.getenv("JWT_SECRET")
    auth_mode = (os.getenv("BLOODNET_AUTH_MODE") or "").lower()

    if env_secret:
        secret = env_secret
    elif auth_mode in {"production", "identity-platform"}:
        raise RuntimeError("BLOODNET_JWT_SECRET is required in production; refusing to use the demo fallback secret")
    else:
        secret = "dev-secret-change-in-production-32b"

    if auth_mode in {"production", "identity-platform"} and len(secret.encode("utf-8")) < 32:
        raise RuntimeError("BLOODNET_JWT_SECRET must be at least 32 bytes in production")

    return secret


JWT_SECRET = _resolve_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("BLOODNET_JWT_EXPIRY_HOURS", "24"))
PASSWORD_MIN_LENGTH = int(os.getenv("BLOODNET_PASSWORD_MIN_LENGTH", "12"))
EMAIL_VERIFICATION_EXPIRY_HOURS = int(os.getenv("BLOODNET_EMAIL_VERIFICATION_EXPIRY_HOURS", "24"))
PASSWORD_RESET_EXPIRY_MINUTES = int(os.getenv("BLOODNET_PASSWORD_RESET_EXPIRY_MINUTES", "15"))

# Password hashing context
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,  # Industry standard: 12 rounds
)


# ============================================================================
# PASSWORD HASHING
# ============================================================================


def hash_password(password: str) -> str:
    """Hash a password using bcrypt (12 rounds)."""
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return pwd_context.verify(password, password_hash)


# ============================================================================
# TOKEN GENERATION & VERIFICATION
# ============================================================================


def generate_jwt_token(
    subject_id: str,
    email: str,
    role: str,
    organization_id: Optional[str] = None,
    permissions: Optional[list[str]] = None,
    bank_id: Optional[str] = None,
    hospital_id: Optional[str] = None,
    region_id: Optional[str] = None,
    mfa_verified: bool = False,
) -> tuple[str, int]:
    """
    Generate a JWT token.
    
    Args:
        subject_id: User UUID
        email: User email
        role: User role (from organization membership)
        organization_id: Organization UUID
        permissions: List of permission codes
        bank_id: Bank ID if org is a blood bank
        hospital_id: Hospital ID if org is a hospital
        region_id: Region ID if org is a regional
    
    Returns:
        (token, expiry_timestamp_seconds)
    """
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=JWT_EXPIRY_HOURS)
    
    payload = {
        "sub": subject_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expiry.timestamp()),
    }
    
    if organization_id:
        payload["organization_id"] = organization_id
    if permissions:
        payload["permissions"] = permissions
    if bank_id:
        payload["bank_id"] = bank_id
    if hospital_id:
        payload["hospital_id"] = hospital_id
    if region_id:
        payload["region_id"] = region_id
    if mfa_verified:
        payload["mfa_verified"] = True
    
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, int(expiry.timestamp())


def verify_jwt_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.
    
    Returns the payload if valid, None if expired or invalid.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_jwt_expiry_seconds() -> int:
    """Get JWT token expiry in seconds."""
    return JWT_EXPIRY_HOURS * 3600


# ============================================================================
# EMAIL VERIFICATION TOKENS
# ============================================================================


def generate_email_verification_token() -> tuple[str, str, datetime]:
    """
    Generate an email verification token.
    
    Returns:
        (secret_token, token_hash, expiry_datetime)
    
    The secret_token is sent to the user via email.
    The token_hash is stored in the database.
    """
    secret_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(secret_token.encode()).hexdigest()
    expiry = datetime.now(timezone.utc) + timedelta(hours=EMAIL_VERIFICATION_EXPIRY_HOURS)
    
    return secret_token, token_hash, expiry


def verify_email_token_hash(provided_token: str, stored_hash: str) -> bool:
    """Verify an email verification token against stored hash."""
    provided_hash = hashlib.sha256(provided_token.encode()).hexdigest()
    return secrets.compare_digest(provided_hash, stored_hash)


# ============================================================================
# PASSWORD RESET TOKENS
# ============================================================================


def generate_password_reset_token() -> tuple[str, str, datetime]:
    """
    Generate a password reset token.
    
    Returns:
        (secret_token, token_hash, expiry_datetime)
    
    The secret_token is sent to the user via email/link.
    The token_hash is stored in the database.
    """
    secret_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(secret_token.encode()).hexdigest()
    expiry = datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRY_MINUTES)
    
    return secret_token, token_hash, expiry


def verify_password_reset_token_hash(provided_token: str, stored_hash: str) -> bool:
    """Verify a password reset token against stored hash."""
    provided_hash = hashlib.sha256(provided_token.encode()).hexdigest()
    return secrets.compare_digest(provided_hash, stored_hash)


# ============================================================================
# PASSWORD VALIDATION
# ============================================================================


def validate_password(password: str, password_confirm: str) -> tuple[bool, str]:
    """
    Validate password meets security requirements.
    
    Returns:
        (is_valid, error_message)
    
    Requirements:
    - Minimum length (configurable, default 12)
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one number
    - At least one special character
    """
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        return False, f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
    
    if password != password_confirm:
        return False, "Passwords do not match"
    
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number"
    
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        return False, "Password must contain at least one special character (!@#$%^&* etc.)"
    
    return True, ""


def validate_email(email: str) -> bool:
    """Basic email validation."""
    return email and "@" in email and "." in email.split("@")[1]


# ============================================================================
# RATE LIMITING HELPERS
# ============================================================================


def should_rate_limit(
    attempt_count: int,
    max_attempts: int = 5,
    lockout_minutes: int = 15,
) -> bool:
    """
    Check if request should be rate limited.
    
    This is a simple check - actual rate limiting should use
    Redis or similar for production.
    """
    return attempt_count >= max_attempts
