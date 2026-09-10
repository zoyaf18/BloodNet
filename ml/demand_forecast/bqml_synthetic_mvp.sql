-- Synthetic MVP model. Never use this model for operational decisions.
CREATE OR REPLACE MODEL `bloodnet.demand_forecast_synthetic_mvp_arima`
OPTIONS(
  model_type='ARIMA_PLUS_XREG', time_series_timestamp_col='demand_date',
  time_series_data_col='requested_units', time_series_id_col=['region', 'blood_group'],
  holiday_region='IN', auto_arima=TRUE, data_frequency='DAILY'
) AS
SELECT demand_date, region, blood_group, SUM(requested_units) AS requested_units,
  EXTRACT(DAYOFWEEK FROM demand_date) AS day_of_week,
  EXTRACT(WEEK FROM demand_date) AS week_of_year
FROM `bloodnet.demand_history`
GROUP BY demand_date, region, blood_group;

INSERT INTO `bloodnet.forecast_results`
SELECT FORMAT_TIMESTAMP('%Y%m%dT%H%M%SZ', CURRENT_TIMESTAMP()), CURRENT_DATE(),
  DATE(forecast_timestamp), forecast.region, forecast.blood_group, 'ALL',
  CAST(forecast.forecast_value AS NUMERIC), CAST(forecast.prediction_interval_lower_bound AS NUMERIC),
  CAST(forecast.prediction_interval_upper_bound AS NUMERIC), CAST(COALESCE(supply.projected_supply, 0) AS NUMERIC),
  IF(forecast.forecast_value > COALESCE(supply.projected_supply, 0), 1.0, 0.0), 0.95, 'synthetic_mvp_arima_plus_xreg_v1', CURRENT_TIMESTAMP()
FROM ML.FORECAST(
  MODEL `bloodnet.demand_forecast_synthetic_mvp_arima`,
  STRUCT(7 AS horizon, 0.95 AS confidence_level),
  (
    SELECT future_date AS demand_date,
      EXTRACT(DAYOFWEEK FROM future_date) AS day_of_week,
      EXTRACT(WEEK FROM future_date) AS week_of_year
    FROM (SELECT DISTINCT region, blood_group FROM `bloodnet.demand_history`) series
    CROSS JOIN UNNEST(GENERATE_DATE_ARRAY(
      CURRENT_DATE(),
      DATE_ADD(CURRENT_DATE(), INTERVAL 6 DAY)
    )) AS future_date
  )
) forecast
LEFT JOIN `bloodnet.supply_projection` supply
  ON DATE(forecast.forecast_timestamp) = supply.target_date
 AND forecast.region = supply.region
 AND forecast.blood_group = supply.blood_group;
