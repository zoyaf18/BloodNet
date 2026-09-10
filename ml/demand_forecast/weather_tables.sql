CREATE TABLE IF NOT EXISTS `bloodnet.weather_observations` (
  observation_date DATE NOT NULL,
  region STRING NOT NULL,
  latitude FLOAT64 NOT NULL,
  longitude FLOAT64 NOT NULL,
  data_kind STRING NOT NULL,
  temperature_c FLOAT64 NOT NULL,
  precipitation_mm FLOAT64 NOT NULL,
  rain_mm FLOAT64 NOT NULL,
  weather_code INT64 NOT NULL,
  weather_flag BOOL NOT NULL,
  ingested_at TIMESTAMP NOT NULL
)
PARTITION BY observation_date
CLUSTER BY region, data_kind;
