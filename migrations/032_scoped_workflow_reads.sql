-- Support current-state role-scoped reads without loading every case into Python.
CREATE INDEX IF NOT EXISTS workflow_requests_hospital_idx
    ON workflow_requests ((payload->>'hospital_id'));

CREATE INDEX IF NOT EXISTS workflow_cases_inventory_matches_idx
    ON workflow_cases USING gin ((payload->'inventory_matches') jsonb_path_ops);
