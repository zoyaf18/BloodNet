"""BigQuery persistence for validated BloodNet event envelopes."""
from __future__ import annotations

import json
from typing import Any

from contracts.events import EventEnvelope


class BigQueryEventSink:
    """Persist envelopes before acknowledgement, using event_id as insertId."""

    def __init__(self, *, project_id: str, dataset_id: str, table_id: str = "events") -> None:
        try:
            from google.cloud import bigquery
        except ImportError as exc:
            raise RuntimeError("BigQuery event persistence requires google-cloud-bigquery") from exc

        self._client = bigquery.Client(project=project_id)
        self._table = f"{project_id}.{dataset_id}.{table_id}"

    def persist(self, event: EventEnvelope[Any], *, source: str = "pubsub") -> None:
        payload = event.model_dump(mode="json")
        event_payload = payload["payload"]
        case_id = event_payload.get("case_id")
        if case_id is None and isinstance(event_payload.get("case"), dict):
            case_id = event_payload["case"].get("case_id")

        rows = [{
            "event_id": event.event_id,
            "event_type": event.event_type.value,
            "schema_version": event.schema_version,
            "request_id": event.request_id,
            "case_id": case_id,
            "correlation_id": event.correlation_id,
            "timestamp": event.occurred_at.isoformat(),
            "payload": json.dumps(event_payload, separators=(",", ":")),
            "source": source,
        }]
        errors = self._client.insert_rows_json(self._table, rows, row_ids=[event.event_id])
        if errors:
            raise RuntimeError(f"BigQuery event persistence failed: {errors}")