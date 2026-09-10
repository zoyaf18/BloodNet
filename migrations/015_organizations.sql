-- 015: Organizations (multi-tenancy)
-- Hospitals, blood banks, regional centers, platform

CREATE TABLE IF NOT EXISTS organizations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Identity
    name varchar(255) NOT NULL,
    type varchar(50) NOT NULL,
    CHECK (type IN ('hospital', 'blood_bank', 'regional', 'platform')),
    
    -- Contact
    address varchar(500),
    contact_email varchar(255) NOT NULL,
    contact_phone varchar(20),
    
    -- Metadata for org-specific fields
    metadata jsonb DEFAULT '{}'::jsonb,
    
    -- Verification
    verified boolean NOT NULL DEFAULT false,
    verified_at timestamptz,
    verified_by uuid,  -- FK to users.id, reference added later
    
    -- Status
    status varchar(50) NOT NULL DEFAULT 'pending'::text,
    CHECK (status IN ('pending', 'active', 'suspended')),
    
    -- Audit
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Indices
CREATE INDEX idx_organizations_type ON organizations(type);
CREATE INDEX idx_organizations_status ON organizations(status);
CREATE INDEX idx_organizations_created_at ON organizations(created_at DESC);

-- Trigger for updated_at
CREATE OR REPLACE FUNCTION update_organizations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER organizations_updated_at_trigger
    BEFORE UPDATE ON organizations
    FOR EACH ROW
    EXECUTE FUNCTION update_organizations_updated_at();

-- Platform organization (singleton, created via seed)
-- INSERT INTO organizations (id, name, type, contact_email, status, verified, verified_at)
-- VALUES (gen_random_uuid(), 'BloodNet Platform', 'platform', 'admin@bloodnet.local', 'active', true, now());
