-- 021: Track the last donor donation date for six-month eligibility calculation
ALTER TABLE donor_profiles
    ADD COLUMN IF NOT EXISTS last_donation_at timestamptz;