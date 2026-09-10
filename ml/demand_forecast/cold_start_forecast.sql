-- Conservative cold-start forecast used when BQML has insufficient history.
-- This is advisory only and is labeled separately from ARIMA_PLUS_XREG.
DELETE FROM `bloodnet.forecast_results`
WHERE forecast_date = CURRENT_DATE();

INSERT INTO `bloodnet.forecast_results`
SELECT
  FORMAT_TIMESTAMP('%Y%m%dT%H%M%SZ', CURRENT_TIMESTAMP()) AS forecast_run_id,
  CURRENT_DATE() AS forecast_date,
  target_date,
  series.region,
  series.blood_group,
  'ALL' AS component,
  CAST(series.latest_requested_units AS NUMERIC) AS predicted_demand,
  CAST(0 AS NUMERIC) AS lower_bound,
  CAST(series.latest_requested_units AS NUMERIC) AS upper_bound,
  CAST(COALESCE(supply.projected_supply, 0) AS NUMERIC) AS projected_supply,
  IF(series.latest_requested_units > COALESCE(supply.projected_supply, 0), 1.0, 0.0) AS shortage_probability,
  0.95 AS confidence_level,
  'deterministic_cold_start_v1' AS model_version,
  CURRENT_TIMESTAMP() AS created_at
FROM (
  SELECT region, blood_group, SUM(requested_units) AS latest_requested_units
  FROM `bloodnet.demand_history`
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY region, blood_group ORDER BY demand_date DESC
  ) = 1
  GROUP BY region, blood_group, demand_date
) AS series
CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(
  CURRENT_DATE(),
  DATE_ADD(CURRENT_DATE(), INTERVAL 6 DAY)
)) AS target_date
LEFT JOIN `bloodnet.supply_projection` supply
  ON supply.target_date = target_date
 AND supply.region = series.region
 AND supply.blood_group = series.blood_group;
