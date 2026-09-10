ALTER TABLE notification_outbox
    ADD COLUMN IF NOT EXISTS last_failure_at timestamptz,
    ADD COLUMN IF NOT EXISTS next_retry_at timestamptz,
    ADD COLUMN IF NOT EXISTS terminal_failure boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS notification_outbox_retry_idx
    ON notification_outbox (terminal_failure, status, next_retry_at);