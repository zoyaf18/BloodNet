-- Keep the live regional graph refresh bounded as workflow volume grows.
CREATE INDEX IF NOT EXISTS workflow_requests_region_updated_idx
    ON workflow_requests (lower(payload->>'region'), updated_at DESC);

CREATE INDEX IF NOT EXISTS workflow_cases_request_id_idx
    ON workflow_cases ((payload->>'request_id'));

CREATE INDEX IF NOT EXISTS inventory_units_available_bank_idx
    ON inventory_units ((payload->>'status'), (payload->>'bank_id'));
