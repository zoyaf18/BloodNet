-- 027: Per-user service region preference. This is intentionally separate
-- from an organization region, which determines authorized data scope.

CREATE TABLE IF NOT EXISTS user_region_preferences (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    region_id varchar(80) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_region_preferences_region
    ON user_region_preferences(region_id);
