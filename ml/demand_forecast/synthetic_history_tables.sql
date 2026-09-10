CREATE TABLE IF NOT EXISTS `bloodnet.forecast_dataset_metadata` (
  dataset_label STRING NOT NULL,
  source_type STRING NOT NULL,
  generator_version STRING NOT NULL,
  seed INT64,
  first_date DATE,
  last_date DATE,
  holdout_start DATE,
  holdout_end DATE,
  holdout_days INT64,
  holdout_rows INT64,
  baseline_mae_units FLOAT64,
  locations ARRAY<STRING>,
  blood_groups ARRAY<STRING>
);

CREATE TABLE IF NOT EXISTS `bloodnet.demand_history` (
  demand_date DATE NOT NULL,
  region STRING NOT NULL,
  hospital_id STRING NOT NULL,
  blood_group STRING NOT NULL,
  requested_units INT64 NOT NULL,
  fulfilled_units INT64 NOT NULL,
  shortage_units INT64 NOT NULL
);