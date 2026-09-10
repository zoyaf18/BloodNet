CREATE TABLE IF NOT EXISTS demand_history (
    demand_date DATE NOT NULL,
    region TEXT NOT NULL,
    hospital_id TEXT NOT NULL,
    blood_group TEXT NOT NULL,
    component TEXT NOT NULL,
    requested_units INTEGER NOT NULL CHECK (requested_units >= 0),
    fulfilled_units INTEGER NOT NULL CHECK (fulfilled_units >= 0),
    shortage_units INTEGER NOT NULL CHECK (shortage_units >= 0),
    PRIMARY KEY (demand_date, region, hospital_id, blood_group, component)
);