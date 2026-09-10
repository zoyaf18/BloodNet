CREATE TABLE IF NOT EXISTS workflow_case_projections (
    case_id TEXT PRIMARY KEY,
    ranked_donors JSONB NOT NULL DEFAULT '[]'::jsonb,
    swarm_status JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS workflow_case_projections_updated_idx
    ON workflow_case_projections (updated_at);
