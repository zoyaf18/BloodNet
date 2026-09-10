-- 016: Organization memberships (users belong to orgs with roles)

CREATE TABLE IF NOT EXISTS organization_memberships (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Foreign keys
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    
    -- Role within org
    role varchar(50) NOT NULL,
    CHECK (role IN ('donor', 'hospital_coordinator', 'bank_admin', 'regional_admin', 'auditor')),
    
    -- Unique: one role per user per org
    CONSTRAINT uq_user_org UNIQUE (user_id, organization_id),
    
    -- Status
    status varchar(50) NOT NULL DEFAULT 'active'::text,
    CHECK (status IN ('active', 'suspended', 'pending_approval', 'rejected', 'invited')),
    
    -- Approval chain
    invited_by uuid REFERENCES users(id) ON DELETE SET NULL,
    approved_by uuid REFERENCES users(id) ON DELETE SET NULL,
    approved_at timestamptz,
    
    -- Expiry (for temporary memberships)
    expires_at timestamptz,
    
    -- Role-specific metadata
    metadata jsonb DEFAULT '{}'::jsonb,
    
    -- Audit
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Indices
CREATE INDEX idx_organization_memberships_user_id ON organization_memberships(user_id);
CREATE INDEX idx_organization_memberships_organization_id ON organization_memberships(organization_id);
CREATE INDEX idx_organization_memberships_status ON organization_memberships(status);
CREATE INDEX idx_organization_memberships_role ON organization_memberships(role);
CREATE INDEX idx_organization_memberships_created_at ON organization_memberships(created_at DESC);

-- Query: find all orgs for a user
CREATE INDEX idx_memberships_user_status ON organization_memberships(user_id, status) WHERE status = 'active';

-- Trigger for updated_at
CREATE OR REPLACE FUNCTION update_organization_memberships_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER organization_memberships_updated_at_trigger
    BEFORE UPDATE ON organization_memberships
    FOR EACH ROW
    EXECUTE FUNCTION update_organization_memberships_updated_at();
