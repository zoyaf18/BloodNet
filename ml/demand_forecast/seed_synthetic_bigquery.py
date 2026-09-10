"""Load synthetic MVP history into BigQuery for forecast-pipeline validation."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from synthetic_history import generate_synthetic_history, holdout_summary

SQL_DIR = Path(__file__).resolve().parent


def seed_synthetic_history(*, project_id: str, dataset_id: str = "bloodnet", years: int = 3, holdout_days: int = 28) -> dict:
    from google.cloud import bigquery

    rows, metadata = generate_synthetic_history(years=years, holdout_days=holdout_days)
    metadata.update(holdout_summary(rows, metadata))
    client = bigquery.Client(project=project_id)
    setup_sql = (SQL_DIR / "synthetic_history_tables.sql").read_text().replace(
        "`bloodnet.", f"`{project_id}.{dataset_id}."
    )
    client.query(setup_sql).result()
    errors = client.load_table_from_json(
        [row.as_dict() for row in rows], f"{project_id}.{dataset_id}.demand_history",
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result().errors
    if errors:
        raise RuntimeError(f"synthetic demand_history load failed: {errors}")
    metadata_row = {"dataset_label": "synthetic_mvp", **metadata}
    errors = client.load_table_from_json(
        [metadata_row], f"{project_id}.{dataset_id}.forecast_dataset_metadata",
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_APPEND"),
    ).result().errors
    if errors:
        raise RuntimeError(f"synthetic metadata load failed: {errors}")
    summary = {"rows": len(rows), **metadata}
    print(summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--dataset", default=os.getenv("BLOODNET_BIGQUERY_DATASET", "bloodnet"))
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--holdout-days", type=int, default=28)
    options = parser.parse_args()
    if not options.project:
        raise SystemExit("--project or GCP_PROJECT_ID/GOOGLE_CLOUD_PROJECT is required")
    seed_synthetic_history(project_id=options.project, dataset_id=options.dataset, years=options.years, holdout_days=options.holdout_days)