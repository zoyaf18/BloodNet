CREATE TABLE IF NOT EXISTS network_graph_edges (
    edge_id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    blood_group TEXT,
    component TEXT,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (region, source_id, target_id, edge_type, blood_group, component)
);

CREATE INDEX IF NOT EXISTS network_graph_edges_region_idx
    ON network_graph_edges (region, edge_type, updated_at DESC);

CREATE TABLE IF NOT EXISTS network_analysis_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL,
    source_window_days INTEGER NOT NULL DEFAULT 30,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS network_analysis_snapshots_region_idx
    ON network_analysis_snapshots (region, analysis_type, created_at DESC);
