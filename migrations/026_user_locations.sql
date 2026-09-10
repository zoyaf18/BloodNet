-- 026: Shared current location for proximity-based workflows across all roles

CREATE TABLE IF NOT EXISTS user_locations (
    user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng double precision NOT NULL CHECK (lng BETWEEN -180 AND 180),
    accuracy_m double precision CHECK (accuracy_m IS NULL OR accuracy_m >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_locations_coordinates ON user_locations(lat, lng);