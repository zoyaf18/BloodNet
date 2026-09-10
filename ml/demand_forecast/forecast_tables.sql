-- BigQuery tables consumed by the Blood Weather API and daily forecast job.
CREATE TABLE IF NOT EXISTS `bloodnet.forecast_results` (
  forecast_run_id STRING NOT NULL,
  forecast_date DATE NOT NULL,
  target_date DATE NOT NULL,
  region STRING NOT NULL,
  blood_group STRING NOT NULL,
  component STRING,
  predicted_demand NUMERIC NOT NULL,
  lower_bound NUMERIC NOT NULL,
  upper_bound NUMERIC NOT NULL,
  projected_supply NUMERIC NOT NULL,
  shortage_probability FLOAT64 NOT NULL,
  confidence_level FLOAT64 NOT NULL,
  model_version STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY target_date
CLUSTER BY region, blood_group;

CREATE TABLE IF NOT EXISTS `bloodnet.supply_projection` (
  target_date DATE NOT NULL,
  region STRING NOT NULL,
  blood_group STRING NOT NULL,
  component STRING,
  projected_supply NUMERIC NOT NULL
)
PARTITION BY target_date
CLUSTER BY region, blood_group;
