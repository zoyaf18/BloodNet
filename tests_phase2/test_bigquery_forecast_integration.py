import os

import pytest


pytestmark = pytest.mark.skipif(
    not (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT_ID"))
    or os.getenv("BLOODNET_RUN_BIGQUERY_TESTS") != "1",
    reason="Set BLOODNET_RUN_BIGQUERY_TESTS=1 with Google credentials to run BigQuery integration tests",
)


def test_bigquery_forecast_tables_exist_and_are_queryable():
    from google.cloud import bigquery

    project = os.getenv("GCP_PROJECT_ID") or os.environ["GOOGLE_CLOUD_PROJECT"]
    dataset = os.getenv("BLOODNET_BIGQUERY_DATASET", "bloodnet")
    client = bigquery.Client(project=project)
    for table in ("demand_history", "forecast_results", "supply_projection"):
        assert client.get_table(f"{project}.{dataset}.{table}").table_id == table
