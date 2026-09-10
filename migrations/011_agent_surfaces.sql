CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS donor_pool (
    donor_id TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    blood_group TEXT NOT NULL,
    radius_km NUMERIC NOT NULL CHECK (radius_km >= 0),
    eligible BOOLEAN NOT NULL DEFAULT FALSE,
    consent_contactable BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS donor_pool_lookup
    ON donor_pool (region, blood_group, eligible, consent_contactable, radius_km);

CREATE TABLE IF NOT EXISTS sop_documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    citation TEXT NOT NULL,
    embedding vector(768),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sop_documents_embedding_idx
    ON sop_documents USING hnsw (embedding vector_cosine_ops);
