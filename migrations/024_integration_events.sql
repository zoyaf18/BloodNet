CREATE TABLE IF NOT EXISTS integration_events (
    source_system TEXT NOT NULL,
    source_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source_system, source_event_id)
);
