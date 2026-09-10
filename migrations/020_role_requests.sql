-- 020: Explicit requests for elevated BloodNet roles

CREATE TABLE IF NOT EXISTS role_access_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    requested_role varchar(50) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'pending',
    notes text,
    reviewed_by uuid REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (requested_role IN ('hospital_coordinator', 'bank_admin', 'regional_admin', 'auditor')),
    CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT uq_role_access_request UNIQUE (user_id, organization_id, requested_role, status)
);

CREATE INDEX idx_role_access_requests_status ON role_access_requests(status, created_at DESC);
CREATE INDEX idx_role_access_requests_user ON role_access_requests(user_id, created_at DESC);

CREATE OR REPLACE FUNCTION update_role_access_requests_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER role_access_requests_updated_at_trigger
    BEFORE UPDATE ON role_access_requests
    FOR EACH ROW
    EXECUTE FUNCTION update_role_access_requests_updated_at();