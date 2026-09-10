-- 020: Donor onboarding profile and contact preferences
CREATE TABLE IF NOT EXISTS donor_profiles (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    blood_group varchar(10) NOT NULL,
    date_of_birth date,
    city varchar(120),
    availability varchar(30) NOT NULL DEFAULT 'available',
    consent_contact boolean NOT NULL DEFAULT false,
    notification_channels jsonb NOT NULL DEFAULT '["sms"]'::jsonb,
    eligibility_status varchar(30) NOT NULL DEFAULT 'eligible',
    next_eligible_at timestamptz,
    donation_history jsonb NOT NULL DEFAULT '[]'::jsonb,
    consent_updated_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (availability IN ('available', 'paused')),
    CHECK (eligibility_status IN ('eligible', 'ineligible', 'deferred')),
    CHECK (jsonb_typeof(notification_channels) = 'array'),
    CHECK (jsonb_typeof(donation_history) = 'array')
);
CREATE INDEX IF NOT EXISTS idx_donor_profiles_blood_group ON donor_profiles(blood_group);
