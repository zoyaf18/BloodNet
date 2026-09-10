-- Daily load after workflow_requests has been exported from PostgreSQL to
-- bloodnet.workflow_requests_staging. The staging table contains the
-- request JSON and the source row's created_at timestamp.
--
-- The export must preserve one row per operational request. This query makes
-- the forecasting series dense by joining each observed dimension to a date
-- spine, including zero-demand days.

CREATE OR REPLACE TABLE `bloodnet.demand_history` AS
WITH request_values AS (
  SELECT
    DATE(created_at) AS demand_date,
    COALESCE(JSON_VALUE(payload, '$.request.region'), JSON_VALUE(payload, '$.region')) AS region,
    COALESCE(JSON_VALUE(payload, '$.request.hospital_id'), 'unknown') AS hospital_id,
    COALESCE(JSON_VALUE(payload, '$.request.group'), 'unknown') AS blood_group,
    COALESCE(JSON_VALUE(payload, '$.request.component'), 'unknown') AS component,
    COALESCE(GREATEST(SAFE_CAST(JSON_VALUE(payload, '$.request.qty') AS INT64), 0), 0) AS requested_units,
    COALESCE(GREATEST(SAFE_CAST(JSON_VALUE(payload, '$.request.fulfilled_units') AS INT64), 0), 0) AS fulfilled_units
  FROM `bloodnet.workflow_requests_staging`
  WHERE COALESCE(JSON_VALUE(payload, '$.request.region'), JSON_VALUE(payload, '$.region')) IS NOT NULL
),
daily AS (
  SELECT
    demand_date, region, hospital_id, blood_group, component,
    SUM(requested_units) AS requested_units,
    LEAST(SUM(requested_units), SUM(fulfilled_units)) AS fulfilled_units
  FROM request_values
  GROUP BY demand_date, region, hospital_id, blood_group, component
),
dimensions AS (
  SELECT DISTINCT region, hospital_id, blood_group, component FROM daily
),
date_spine AS (
  SELECT demand_date
  FROM UNNEST(GENERATE_DATE_ARRAY(
    COALESCE((SELECT MIN(demand_date) FROM daily), CURRENT_DATE()),
    COALESCE((SELECT MAX(demand_date) FROM daily), CURRENT_DATE())
  )) AS demand_date
)
SELECT
  date_spine.demand_date,
  dimensions.region,
  dimensions.hospital_id,
  dimensions.blood_group,
  dimensions.component,
  COALESCE(daily.requested_units, 0) AS requested_units,
  COALESCE(daily.fulfilled_units, 0) AS fulfilled_units,
  COALESCE(daily.requested_units, 0) - COALESCE(daily.fulfilled_units, 0) AS shortage_units
FROM date_spine
CROSS JOIN dimensions
LEFT JOIN daily USING (demand_date, region, hospital_id, blood_group, component);
