-- 019: Email verification and password reset tokens
-- Store temporary tokens for email verification and password reset workflows

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Which user and email
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email varchar(255) NOT NULL,
    
    -- Token hash (SHA256 of the secret token sent to user)
    token_hash varchar(64) NOT NULL UNIQUE,
    
    -- Lifecycle
    status varchar(50) NOT NULL DEFAULT 'pending'::text,
    CHECK (status IN ('pending', 'verified', 'expired')),
    
    expires_at timestamptz NOT NULL,
    verified_at timestamptz,
    
    -- Audit
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Indices
CREATE INDEX idx_email_verification_tokens_user_id ON email_verification_tokens(user_id);
CREATE INDEX idx_email_verification_tokens_token_hash ON email_verification_tokens(token_hash);
CREATE INDEX idx_email_verification_tokens_status ON email_verification_tokens(status);
CREATE INDEX idx_email_verification_tokens_expires_at ON email_verification_tokens(expires_at);

-- Find pending token for user
CREATE INDEX idx_email_verification_tokens_user_pending ON email_verification_tokens(user_id, status) 
    WHERE status = 'pending';


CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Which user
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Token hash (SHA256 of the secret token sent to user)
    token_hash varchar(64) NOT NULL UNIQUE,
    
    -- Lifecycle
    status varchar(50) NOT NULL DEFAULT 'pending'::text,
    CHECK (status IN ('pending', 'used', 'expired')),
    
    expires_at timestamptz NOT NULL,
    used_at timestamptz,
    
    -- Audit
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Indices
CREATE INDEX idx_password_reset_tokens_user_id ON password_reset_tokens(user_id);
CREATE INDEX idx_password_reset_tokens_token_hash ON password_reset_tokens(token_hash);
CREATE INDEX idx_password_reset_tokens_status ON password_reset_tokens(status);
CREATE INDEX idx_password_reset_tokens_expires_at ON password_reset_tokens(expires_at);

-- Find pending reset token for user
CREATE INDEX idx_password_reset_tokens_user_pending ON password_reset_tokens(user_id, status) 
    WHERE status = 'pending';
