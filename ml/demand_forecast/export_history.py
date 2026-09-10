"""Step 7A: export real workflow request history from Cloud SQL to BigQuery.

Run this from Cloud Shell with Application Default Credentials. The database
password is read from BLOODNET_DB_PASSWORD and is never logged.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.cloud.sql.connector import Connector, IPTypes
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "match-svc"))
from forecast_service import build_demand_history


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--dataset", default=os.getenv("BLOODNET_BIGQUERY_DATASET", "bloodnet"))
    parser.add_argument("--instance", default=os.getenv("BLOODNET_CLOUD_SQL_INSTANCE"))
    parser.add_argument("--database", default=os.getenv("BLOODNET_DB_NAME", "bloodnet"))
    parser.add_argument("--user", default=os.getenv("BLOODNET_DB_USER", "bloodnet"))
    parser.add_argument("--region", default=os.getenv("BLOODNET_DEFAULT_REGION", "Pune"))
    return parser.parse_args()


def _fetch_requests(args: argparse.Namespace) -> list[dict[str, Any]]:
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if database_url:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            return list(connection.execute("SELECT payload, created_at FROM workflow_requests ORDER BY created_at").fetchall())
    password = os.getenv("BLOODNET_DB_PASSWORD")
    if not password:
        raise RuntimeError("BLOODNET_DB_PASSWORD must be set in the Cloud Shell environment")
    if not args.instance:
        raise RuntimeError("--instance or BLOODNET_CLOUD_SQL_INSTANCE is required")
    connector = Connector(ip_type=IPTypes.PUBLIC)
    try:
        connection = connector.connect(
            args.instance,
            "pg8000",
            user=args.user,
            password=password,
            db=args.database,
        )
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT payload, created_at FROM workflow_requests ORDER BY created_at")
            return [{"payload": payload, "created_at": created_at} for payload, created_at in cursor.fetchall()]
        finally:
            connection.close()
    finally:
        connector.close()


def export_history(args: argparse.Namespace) -> dict[str, Any]:
    if not args.project:
        raise RuntimeError("--project or GCP_PROJECT_ID/GOOGLE_CLOUD_PROJECT is required")
    request_rows = _fetch_requests(args)
    history = build_demand_history(request_rows, default_region=args.region)
    if not history:
        raise RuntimeError(
            "workflow_requests contains no usable historical rows "
            f"(fetched {len(request_rows)} rows); refusing to train BQML. "
            "Rows require created_at plus request.group and qty."
        )
    invalid = [row for row in history if row.requested_units < 0 or row.fulfilled_units < 0 or row.shortage_units < 0]
    if invalid:
        raise RuntimeError(f"demand history contains {len(invalid)} invalid rows")

    client = bigquery.Client(project=args.project)
    table_id = f"{args.project}.{args.dataset}.demand_history"
    errors = client.load_table_from_json(
        [row.model_dump(mode="json") for row in history],
        table_id,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result().errors
    if errors:
        raise RuntimeError(f"BigQuery demand_history load failed: {errors}")

    dimensions = Counter((row.region, row.blood_group) for row in history)
    summary = {
        "rows": len(history),
        "earliest_date": min(row.demand_date for row in history).isoformat(),
        "latest_date": max(row.demand_date for row in history).isoformat(),
        "regions": sorted({row.region for row in history}),
        "blood_groups": sorted({row.blood_group for row in history}),
        "components": "retained in source workflow requests; excluded from Blood Weather series",
        "series": len(dimensions),
    }
    print(summary)
    return summary


if __name__ == "__main__":
    export_history(_args())