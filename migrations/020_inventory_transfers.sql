CREATE TABLE IF NOT EXISTS inventory_transfers (
    transfer_id text PRIMARY KEY,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'in_transit',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS inventory_transfers_status_idx
    ON inventory_transfers (status, created_at);