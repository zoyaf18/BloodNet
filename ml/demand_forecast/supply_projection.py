"""Materialize forecast supply from canonical PostgreSQL inventory."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def materialize_supply_projection(
    *,
    client: Any,
    database_url: str,
    project_id: str,
    dataset_id: str,
    horizon_days: int = 7,
) -> int:
    """Replace the seven-day BigQuery supply snapshot with database inventory."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        organizations = connection.execute(
            "SELECT id::text, metadata FROM organizations "
            "WHERE status = 'active' AND type = 'blood_bank'"
        ).fetchall()
        inventory_rows = connection.execute("SELECT payload FROM inventory_units").fetchall()
        case_rows = connection.execute(
            "SELECT c.payload AS case_payload, r.payload AS request_payload "
            "FROM workflow_cases c JOIN workflow_requests r "
            "ON r.request_id = c.payload->>'request_id'"
        ).fetchall()

    bank_regions: dict[str, set[str]] = {}
    for organization in organizations:
        metadata = organization["metadata"] or {}
        regions = {
            value.strip() for value in (
                metadata.get("region_id"), metadata.get("region"), metadata.get("city")
            ) if isinstance(value, str) and value.strip()
        }
        for bank_id in {str(metadata.get("bank_id") or organization["id"]), str(organization["id"])}:
            bank_regions.setdefault(bank_id, set()).update(regions)
    for row in case_rows:
        request_payload = row["request_payload"] or {}
        request = request_payload.get("request", request_payload)
        region = request.get("region") or request_payload.get("region")
        if not isinstance(region, str) or not region.strip():
            continue
        for match in (row["case_payload"] or {}).get("inventory_matches", []):
            bank_id = match.get("bank_id")
            if bank_id:
                bank_regions.setdefault(str(bank_id), set()).add(region.strip())

    dimensions = {
        (str(row["region"]), str(row["blood_group"]))
        for row in client.query(
            f"SELECT DISTINCT region, blood_group "
            f"FROM `{project_id}.{dataset_id}.demand_history`"
        ).result()
    }
    available_units: list[tuple[str, str, datetime]] = []
    for row in inventory_rows:
        payload = row["payload"] or {}
        regions = bank_regions.get(str(payload.get("bank_id", "")), set())
        expires_at = _as_datetime(payload.get("expires_at"))
        if regions and payload.get("status") == "available" and expires_at:
            for region in regions:
                dimension = (region, str(payload.get("group", "")))
                dimensions.add(dimension)
                available_units.append((*dimension, expires_at))

    today = datetime.now(timezone.utc).date()
    rows = []
    for offset in range(horizon_days):
        target_date = today + timedelta(days=offset)
        for region, blood_group in sorted(dimensions):
            projected_supply = sum(
                1 for unit_region, unit_group, expires_at in available_units
                if (unit_region, unit_group) == (region, blood_group)
                and expires_at.date() >= target_date
            )
            rows.append({
                "target_date": target_date.isoformat(),
                "region": region,
                "blood_group": blood_group,
                # Existing BigQuery schemas require this compatibility value;
                # Blood Weather excludes it from its public response.
                "component": "ALL",
                "projected_supply": projected_supply,
            })

    table_id = f"{project_id}.{dataset_id}.supply_projection"
    client.query(
        f"DELETE FROM `{table_id}` WHERE target_date >= CURRENT_DATE() "
        f"AND target_date < DATE_ADD(CURRENT_DATE(), INTERVAL {int(horizon_days)} DAY)"
    ).result()
    if rows:
        client.load_table_from_json(rows, table_id).result()
    return len(rows)
