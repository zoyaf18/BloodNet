"""Daily BigQuery/BQML Blood Weather job entrypoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from contracts.observability import configure_logging, log_event


LOGGER = configure_logging()
SQL_DIR = Path(__file__).resolve().parent
FORECAST_HORIZON_DAYS = 7


def run_forecast_job(*, project_id: str, dataset_id: str = "bloodnet", refresh_history: bool = False, synthetic_mvp: bool = False) -> dict[str, Any]:
    """Refresh supply and forecast rows, using a labeled cold start if needed."""
    from google.cloud import bigquery
    from weather_ingest import ingest_weather

    client = bigquery.Client(project=project_id)
    statements = [("forecast_tables", SQL_DIR / "forecast_tables.sql")]
    if refresh_history:
        statements.append(("demand_history_load", SQL_DIR / "demand_history_load.sql"))
    statements.append(("bqml_forecast", SQL_DIR / ("bqml_synthetic_mvp.sql" if synthetic_mvp else "bqml_baseline.sql")))
    weather_available = True
    step = "weather_ingestion"
    try:
        weather_sql = (SQL_DIR / "weather_tables.sql").read_text().replace(
            "`bloodnet.", f"`{project_id}.{dataset_id}."
        )
        client.query(weather_sql).result()
        ingest_weather(project_id=project_id, dataset_id=dataset_id)
        from supply_projection import materialize_supply_projection
        database_url = os.getenv("BLOODNET_DATABASE_URL")
        if not database_url:
            raise RuntimeError("BLOODNET_DATABASE_URL is required to project live inventory")
        step = "supply_projection"
        log_event(LOGGER, "forecast_job_step_started", service="forecast-job", step=step)
        materialize_supply_projection(
            client=client,
            database_url=database_url,
            project_id=project_id,
            dataset_id=dataset_id,
            horizon_days=FORECAST_HORIZON_DAYS,
        )
        log_event(LOGGER, "forecast_job_step_succeeded", service="forecast-job", step=step)
        for step, path in statements:
            sql = path.read_text().replace("`bloodnet.", f"`{project_id}.{dataset_id}.")
            log_event(LOGGER, "forecast_job_step_started", service="forecast-job", step=step)
            client.query(sql).result()
            log_event(LOGGER, "forecast_job_step_succeeded", service="forecast-job", step=step)
    except Exception as error:
        error_text = str(error).lower()
        insufficient_history = (
            "all time series failed to fit" in error_text
            or "all time series failed" in error_text and "invalid" in error_text
        )
        weather_available = False if "weather" in error_text or "open-meteo" in error_text else weather_available
        if (step == "bqml_forecast" and insufficient_history) or not weather_available:
            fallback_path = SQL_DIR / "cold_start_forecast.sql"
            fallback_sql = fallback_path.read_text().replace(
                "`bloodnet.", f"`{project_id}.{dataset_id}."
            )
            log_event(
                LOGGER,
                "forecast_job_bqml_fallback",
                service="forecast-job",
                reason="insufficient_or_invalid_history",
            )
            try:
                client.query(fallback_sql).result()
                log_event(
                    LOGGER,
                    "forecast_job_fallback_succeeded",
                    service="forecast-job",
                    step="cold_start_forecast",
                )
                return {
                    "status": "success",
                    "horizon_days": FORECAST_HORIZON_DAYS,
                    "model_version": "deterministic_cold_start_v1",
                    "degraded": True,
                }
            except Exception as fallback_error:
                log_event(
                    LOGGER,
                    "forecast_job_failed",
                    service="forecast-job",
                    step="cold_start_forecast",
                    error=str(fallback_error),
                )
                return {"status": "failed", "step": "cold_start_forecast", "error": str(fallback_error)}
        log_event(LOGGER, "forecast_job_failed", service="forecast-job", step=step, error=str(error))
        return {"status": "failed", "step": step, "error": str(error)}
    log_event(LOGGER, "forecast_job_succeeded", service="forecast-job", horizon_days=FORECAST_HORIZON_DAYS)
    return {"status": "success", "horizon_days": FORECAST_HORIZON_DAYS, "model_version": "synthetic_mvp_arima_plus_xreg_v1" if synthetic_mvp else "arima_plus_xreg_v1"}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-history", action="store_true")
    parser.add_argument("--synthetic-mvp", action="store_true", help="Use the explicitly labeled synthetic MVP model")
    options = parser.parse_args()
    project = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise SystemExit("GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT is required")
    result = run_forecast_job(
        project_id=project,
        dataset_id=os.getenv("BLOODNET_BIGQUERY_DATASET", "bloodnet"),
        refresh_history=options.refresh_history,
        synthetic_mvp=options.synthetic_mvp,
    )
    raise SystemExit(0 if result["status"] == "success" else 1)
