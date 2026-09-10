-- Make notification tracing and provider deduplication first-class fields.
ALTER TABLE notification_outbox
ADD COLUMN IF NOT EXISTS correlation_id TEXT,
ADD COLUMN IF NOT EXISTS request_id TEXT,
ADD COLUMN IF NOT EXISTS case_id TEXT,
ADD COLUMN IF NOT EXISTS provider_idempotency_key TEXT;

UPDATE notification_outbox
SET request_id = COALESCE(request_id, payload->>'request_id'),
    case_id = COALESCE(case_id, payload->>'case_id'),
    correlation_id = COALESCE(correlation_id, payload->>'case_id'),
    provider_idempotency_key = COALESCE(provider_idempotency_key, notification_id)
WHERE request_id IS NULL
   OR case_id IS NULL
   OR correlation_id IS NULL
   OR provider_idempotency_key IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS notification_outbox_provider_key_idx
    ON notification_outbox (provider_idempotency_key);