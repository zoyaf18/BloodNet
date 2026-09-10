ALTER TABLE notification_outbox
    ADD COLUMN IF NOT EXISTS provider_message_id text,
    ADD COLUMN IF NOT EXISTS delivery_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS delivery_failure_reason text,
    ADD COLUMN IF NOT EXISTS delivered_at timestamptz;

CREATE INDEX IF NOT EXISTS notification_outbox_delivery_status_idx
    ON notification_outbox (delivery_status, updated_at);