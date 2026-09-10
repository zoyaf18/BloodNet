-- 014: Core authentication users table
-- This is the foundational table for all user accounts

CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Identity linkage
    identity_subject varchar(255) UNIQUE,  -- External IdP subject (e.g., GCP Identity Platform)
    
    -- Credentials
    email varchar(255) NOT NULL UNIQUE,
    email_verified boolean NOT NULL DEFAULT false,
    email_verified_at timestamptz,
    password_hash varchar(255),  -- bcrypt hash, nullable for external IdP
    
    -- Profile
    display_name varchar(255),
    phone varchar(20),
    
    -- Password reset
    password_reset_token varchar(255) UNIQUE,
    password_reset_expires_at timestamptz,
    
    -- MFA
    mfa_enabled boolean NOT NULL DEFAULT false,
    mfa_secret varchar(255),  -- encrypted TOTP secret
    
    -- Account status
    status varchar(50) NOT NULL DEFAULT 'registered'::text,
    -- registered -> email_unverified -> active (self-reg)
    -- registered -> email_unverified -> pending_approval -> active (org approval)
    -- active -> suspended, deactivated
    CHECK (status IN ('registered', 'email_unverified', 'pending_approval', 'active', 'suspended', 'deactivated')),
    
    -- Audit
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid,  -- FK to users.id, reference added later
    updated_by uuid,  -- FK to users.id, reference added later
    
    -- Indexing
    CONSTRAINT users_email_lowercase CHECK (email = LOWER(email))
);

-- Indices for performance
CREATE INDEX idx_users_email ON users(LOWER(email));
CREATE INDEX idx_users_identity_subject ON users(identity_subject);
CREATE INDEX idx_users_status ON users(status);
CREATE INDEX idx_users_created_at ON users(created_at DESC);
CREATE INDEX idx_users_password_reset_token ON users(password_reset_token);

-- Audit trigger (updated_at)
CREATE OR REPLACE FUNCTION update_users_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_updated_at_trigger
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_users_updated_at();
