CREATE TABLE IF NOT EXISTS donor_responses (
    outreach_id TEXT NOT NULL,
    donor_id TEXT NOT NULL,
    response TEXT NOT NULL,
    responded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (outreach_id, donor_id)
);