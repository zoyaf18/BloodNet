"""Fetch Open-Meteo daily weather features into BigQuery."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from typing import Any

from contracts.observability import configure_logging, log_event

LOGGER = configure_logging()
WEATHER_API = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_REGIONS = {"Pune": (18.5204, 73.8567)}
SQL_DIR = Path(__file__).resolve().parent


def configured_regions() -> dict[str, tuple[float, float]]:
    raw = os.getenv("BLOODNET_WEATHER_REGIONS")
    if not raw:
        return DEFAULT_REGIONS
    try:
        values = json.loads(raw)
        return {str(name): (float(value["latitude"]), float(value["longitude"])) for name, value in values.items()}
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("BLOODNET_WEATHER_REGIONS must be a JSON object of latitude/longitude values") from exc


def _fetch_daily(url: str, *, region: str, latitude: float, longitude: float, start: date, end: date, kind: str) -> list[dict[str, Any]]:
    query = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum,rain_sum,weather_code",
        "timezone": "UTC",
    })
    request = Request(f"{url}?{query}", headers={"User-Agent": "BloodNet/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    daily = payload.get("daily") or {}
    rows = []
    for index, observed_date in enumerate(daily.get("time", [])):
        precipitation = float((daily.get("precipitation_sum") or [0])[index] or 0)
        rain = float((daily.get("rain_sum") or [0])[index] or 0)
        temperature = float((daily.get("temperature_2m_mean") or [0])[index] or 0)
        weather_code = int((daily.get("weather_code") or [0])[index] or 0)
        rows.append({
            "observation_date": observed_date,
            "region": region,
            "latitude": latitude,
            "longitude": longitude,
            "data_kind": kind,
            "temperature_c": temperature,
            "precipitation_mm": precipitation,
            "rain_mm": rain,
            "weather_code": weather_code,
            "weather_flag": precipitation >= 10 or temperature >= 38 or weather_code >= 60,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        })
    return rows


def fetch_weather(*, regions: dict[str, tuple[float, float]] | None = None, history_days: int = 30, forecast_days: int = 7) -> list[dict[str, Any]]:
    today = date.today()
    regions = regions or configured_regions()
    rows: list[dict[str, Any]] = []
    for region, (latitude, longitude) in regions.items():
        rows.extend(_fetch_daily(
            ARCHIVE_API, region=region, latitude=latitude, longitude=longitude,
            start=today - timedelta(days=history_days), end=today - timedelta(days=1), kind="observation",
        ))
        rows.extend(_fetch_daily(
            WEATHER_API, region=region, latitude=latitude, longitude=longitude,
            start=today, end=today + timedelta(days=forecast_days), kind="forecast",
        ))
    if not rows:
        raise RuntimeError("Open-Meteo returned no daily weather rows")
    return rows


def ingest_weather(*, project_id: str, dataset_id: str = "bloodnet") -> dict[str, Any]:
    from google.cloud import bigquery

    rows = fetch_weather()
    client = bigquery.Client(project=project_id)
    table_id = f"{project_id}.{dataset_id}.weather_observations"
    schema_path = SQL_DIR / "weather_tables.sql"
    client.query(schema_path.read_text().replace("`bloodnet.", f"`{project_id}.{dataset_id}.")).result()
    staging_id = f"{table_id}_staging"
    client.load_table_from_json(rows, staging_id, job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")).result()
    merge = f"""
    MERGE `{table_id}` target
    USING `{staging_id}` source
    ON target.observation_date = source.observation_date
       AND target.region = source.region
       AND target.data_kind = source.data_kind
    WHEN MATCHED THEN UPDATE SET
      latitude = source.latitude, longitude = source.longitude,
      temperature_c = source.temperature_c, precipitation_mm = source.precipitation_mm,
      rain_mm = source.rain_mm, weather_code = source.weather_code,
      weather_flag = source.weather_flag, ingested_at = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (
            observation_date, region, latitude, longitude, data_kind,
            temperature_c, precipitation_mm, rain_mm, weather_code,
            weather_flag, ingested_at
        ) VALUES (
            source.observation_date, source.region, source.latitude, source.longitude,
            source.data_kind, source.temperature_c, source.precipitation_mm,
            source.rain_mm, source.weather_code, source.weather_flag, source.ingested_at
        );
    DROP TABLE `{staging_id}`;
    """
    client.query(merge).result()
    log_event(LOGGER, "weather_ingestion_succeeded", service="weather-ingest", rows=len(rows))
    return {"status": "success", "rows": len(rows), "regions": sorted({row["region"] for row in rows})}


if __name__ == "__main__":
    project = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise SystemExit("GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT is required")
    result = ingest_weather(project_id=project, dataset_id=os.getenv("BLOODNET_BIGQUERY_DATASET", "bloodnet"))
    raise SystemExit(0 if result["status"] == "success" else 1)
