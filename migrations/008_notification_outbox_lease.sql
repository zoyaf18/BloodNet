-- Add lease-based processing support for crash recovery.
-- Workers now claim rows with a lease that expires after a configured timeout.
-- Expired leases are automatically reclaimed by the recovery process.

ALTER TABLE notification_outbox 
ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ NULL,
ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;

-- Index for finding expired leases during recovery
CREATE INDEX IF NOT EXISTS notification_outbox_expired_leases_idx
    ON notification_outbox (lease_expires_at)
    WHERE status = 'processing' AND lease_expires_at IS NOT NULL;
