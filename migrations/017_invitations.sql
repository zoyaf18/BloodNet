-- 017: Invitations (invite users to join organizations)

CREATE TABLE IF NOT EXISTS invitations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Who is invited where
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email varchar(255) NOT NULL,
    
    -- Role they'll get
    role varchar(50) NOT NULL,
    CHECK (role IN ('donor', 'hospital_coordinator', 'bank_admin', 'regional_admin', 'auditor')),
    
    -- Secure token (hashed for DB storage)
    -- Token = crypto.secrets token (32 bytes base64)
    -- token_hash = SHA256(token) stored in DB
    -- User receives: /invitations/{token}/accept
    token_hash varchar(64) NOT NULL UNIQUE,
    
    -- Status
    status varchar(50) NOT NULL DEFAULT 'pending'::text,
    CHECK (status IN ('pending', 'accepted', 'expired', 'rejected')),
    
    -- Invitation lifecycle
    invited_by uuid NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    expires_at timestamptz NOT NULL,
    accepted_at timestamptz,
    accepted_by uuid REFERENCES users(id) ON DELETE SET NULL,
    
    -- Audit
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Indices
CREATE INDEX idx_invitations_token_hash ON invitations(token_hash);
CREATE INDEX idx_invitations_email ON invitations(LOWER(email));
CREATE INDEX idx_invitations_organization_id ON invitations(organization_id);
CREATE INDEX idx_invitations_status ON invitations(status);
CREATE INDEX idx_invitations_created_at ON invitations(created_at DESC);
CREATE INDEX idx_invitations_expires_at ON invitations(expires_at);

-- Find pending invitations for a user
CREATE INDEX idx_invitations_pending_email ON invitations(LOWER(email), status) WHERE status = 'pending';
