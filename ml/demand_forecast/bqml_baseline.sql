-- BloodNet Demand Forecasting Baseline
-- Using BigQuery ML ARIMA_PLUS_XREG to forecast blood demand using external regressors (weather, holidays)

-- 1. Exported by the daily workflow_requests aggregation job.
-- Zero-demand days must be present in bloodnet.demand_history before training.

-- 2. Train one multi-series model for region x blood group. Components remain
-- in raw demand and inventory, but Blood Weather forecasts planning pressure.
CREATE OR REPLACE MODEL `bloodnet.demand_forecast_arima`
OPTIONS(
  model_type='ARIMA_PLUS_XREG',
  time_series_timestamp_col='demand_date',
  time_series_data_col='requested_units',
  time_series_id_col=['region', 'blood_group'],
  holiday_region='IN',
  auto_arima=TRUE,
  data_frequency='DAILY'
) AS
SELECT
  demand.demand_date,
  demand.region,
  demand.blood_group,
  SUM(demand.requested_units) AS requested_units,
  EXTRACT(DAYOFWEEK FROM demand.demand_date) AS day_of_week,
  EXTRACT(WEEK FROM demand.demand_date) AS week_of_year,
  COALESCE(weather.temperature_c, 0) AS temperature_c,
  COALESCE(weather.precipitation_mm, 0) AS precipitation_mm,
  CAST(COALESCE(weather.weather_flag, FALSE) AS INT64) AS weather_flag
FROM `bloodnet.demand_history` demand
LEFT JOIN `bloodnet.weather_observations` weather
  ON weather.observation_date = demand.demand_date
 AND weather.region = demand.region
 AND weather.data_kind = 'observation'
GROUP BY demand.demand_date, demand.region, demand.blood_group,
  weather.temperature_c, weather.precipitation_mm, weather.weather_flag;

-- 3. Persist the 14-day forecast for API reads. Run as a daily batch job.
INSERT INTO `bloodnet.forecast_results`
SELECT
  FORMAT_TIMESTAMP('%Y%m%dT%H%M%SZ', CURRENT_TIMESTAMP()) AS forecast_run_id,
  CURRENT_DATE() AS forecast_date,
  DATE(forecast_timestamp) AS target_date,
  forecast.region,
  forecast.blood_group,
  'ALL' AS component,
  CAST(forecast.forecast_value AS NUMERIC) AS predicted_demand,
  CAST(forecast.prediction_interval_lower_bound AS NUMERIC) AS lower_bound,
  CAST(forecast.prediction_interval_upper_bound AS NUMERIC) AS upper_bound,
  CAST(COALESCE(supply.projected_supply, 0) AS NUMERIC) AS projected_supply,
  CASE
    WHEN forecast.prediction_interval_upper_bound = forecast.prediction_interval_lower_bound
      THEN IF(forecast.forecast_value > COALESCE(supply.projected_supply, 0), 1.0, 0.0)
    ELSE 1.0 / (1.0 + EXP(LEAST(700.0, GREATEST(-700.0,
      (COALESCE(supply.projected_supply, 0) - forecast.forecast_value) /
      ((forecast.prediction_interval_upper_bound - forecast.prediction_interval_lower_bound) / 7.1)))))
  END AS shortage_probability,
  0.95 AS confidence_level,
  'arima_plus_xreg_v1' AS model_version,
  CURRENT_TIMESTAMP() AS created_at
FROM ML.FORECAST(
  MODEL `bloodnet.demand_forecast_arima`,
  STRUCT(7 AS horizon, 0.95 AS confidence_level),
  (
    WITH series AS (
      SELECT DISTINCT region, blood_group
      FROM `bloodnet.demand_history`
    )
    SELECT
      future_date AS demand_date,
      series.region,
      series.blood_group,
      EXTRACT(DAYOFWEEK FROM future_date) AS day_of_week,
      EXTRACT(WEEK FROM future_date) AS week_of_year,
      COALESCE(weather.temperature_c, 0) AS temperature_c,
      COALESCE(weather.precipitation_mm, 0) AS precipitation_mm,
      CAST(COALESCE(weather.weather_flag, FALSE) AS INT64) AS weather_flag
    FROM series
    CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(
      CURRENT_DATE(),
      DATE_ADD(CURRENT_DATE(), INTERVAL 6 DAY)
    )) AS future_date
    LEFT JOIN `bloodnet.weather_observations` weather
      ON weather.observation_date = future_date
     AND weather.region = series.region
     AND weather.data_kind = 'forecast'
  )
) forecast
LEFT JOIN `bloodnet.supply_projection` supply
  ON DATE(forecast.forecast_timestamp) = supply.target_date
 AND forecast.region = supply.region
 AND forecast.blood_group = supply.blood_group;
