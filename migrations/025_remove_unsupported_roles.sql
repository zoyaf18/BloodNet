-- Remove legacy roles that are outside the canonical five-role model.
-- Patient accounts are downgraded to donor. Legacy platform administrators are
-- converted to regional administrators so an existing deployment retains a
-- legitimate, least-privilege approval path.

UPDATE organization_memberships
SET role = CASE WHEN role = 'platform_admin' THEN 'regional_admin' ELSE 'donor' END
WHERE role IN ('patient', 'platform_admin');

UPDATE invitations
SET role = CASE WHEN role = 'platform_admin' THEN 'regional_admin' ELSE 'donor' END
WHERE role IN ('patient', 'platform_admin');

ALTER TABLE organization_memberships
    DROP CONSTRAINT IF EXISTS organization_memberships_role_check;
ALTER TABLE organization_memberships
    ADD CONSTRAINT organization_memberships_role_check
    CHECK (role IN ('donor', 'hospital_coordinator', 'bank_admin', 'regional_admin', 'auditor'));

ALTER TABLE invitations
    DROP CONSTRAINT IF EXISTS invitations_role_check;
ALTER TABLE invitations
    ADD CONSTRAINT invitations_role_check
    CHECK (role IN ('donor', 'hospital_coordinator', 'bank_admin', 'regional_admin', 'auditor'));
