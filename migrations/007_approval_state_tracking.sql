-- Track approval decisions and their outcomes for atomicity and idempotency
CREATE TABLE IF NOT EXISTS approval_state_tracking (
    approval_id TEXT PRIMARY KEY,
    rec_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    decision TEXT NOT NULL,  -- APPROVED, REJECTED
    audit_id TEXT NOT NULL REFERENCES audit_records(audit_id),
    outbox_event_id TEXT NULL REFERENCES notification_outbox(event_id),
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (rec_id, approval_id)  -- Prevent duplicate processing of same rec
);

CREATE INDEX IF NOT EXISTS approval_state_tracking_case_idx
    ON approval_state_tracking (case_id);

CREATE INDEX IF NOT EXISTS approval_state_tracking_rec_idx
    ON approval_state_tracking (rec_id);
