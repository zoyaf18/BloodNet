-- Provider ingress and delivery evidence used by the operational UI.
CREATE TABLE IF NOT EXISTS messaging_inbound_events (
    provider text NOT NULL CHECK (provider IN ('sms', 'whatsapp')),
    provider_event_id text NOT NULL,
    request_id text NOT NULL,
    hospital_id text NOT NULL,
    sender_fingerprint text NOT NULL,
    status text NOT NULL CHECK (status IN ('processing', 'ingested', 'failed')),
    failure_reason text,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS messaging_inbound_events_hospital_idx
    ON messaging_inbound_events (hospital_id, received_at DESC);

CREATE TABLE IF NOT EXISTS notification_delivery_receipts (
    provider_message_id text NOT NULL,
    delivery_status text NOT NULL,
    failure_reason text,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider_message_id, delivery_status)
);

CREATE INDEX IF NOT EXISTS notification_delivery_receipts_received_idx
    ON notification_delivery_receipts (received_at DESC);
