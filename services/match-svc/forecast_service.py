"""Blood Weather application adapter.

BQML jobs write forecast rows; the API only reads the persisted result set.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import erf, sqrt
import os
from typing import Any, Iterable, Protocol
from uuid import uuid4

from contracts.forecast import DemandHistoryRow, ForecastPoint, ForecastResult

MIN_FORECAST_HORIZON_DAYS = 1
MAX_FORECAST_HORIZON_DAYS = 14


def forecast_is_stale(result: ForecastResult) -> bool:
    return bool(result.points) and max(point.target_date for point in result.points) < date.today()


def validate_horizon_days(horizon_days: int) -> int:
    if not MIN_FORECAST_HORIZON_DAYS <= horizon_days <= MAX_FORECAST_HORIZON_DAYS:
        raise ValueError(
            f"horizon_days must be between {MIN_FORECAST_HORIZON_DAYS} and "
            f"{MAX_FORECAST_HORIZON_DAYS}"
        )
    return horizon_days


class ForecastRepository(Protocol):
    def get_forecast(self, region: str, horizon_days: int) -> ForecastResult | None:
        ...


def build_demand_history(
    request_rows: Iterable[dict[str, Any]],
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    default_region: str | None = None,
) -> list[DemandHistoryRow]:
    """Aggregate request projections and explicitly emit zero-demand days."""
    grouped: dict[tuple[str, str, str, date], dict[str, int]] = defaultdict(
        lambda: {"requested_units": 0, "fulfilled_units": 0}
    )
    dimensions: set[tuple[str, str, str]] = set()
    observed_dates: set[date] = set()

    for row in request_rows:
        payload = row.get("payload", row)
        request = payload.get("request", payload)
        created_at = row.get("created_at") or payload.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if isinstance(created_at, datetime):
            request_date = created_at.date()
        elif isinstance(created_at, date):
            request_date = created_at
        else:
            continue
        region_value = request.get("region") or payload.get("region") or default_region
        if not region_value:
            continue
        region = str(region_value)
        hospital_id = str(request.get("hospital_id", "unknown"))
        blood_group = str(request.get("group", "unknown"))
        component = str(request.get("component", "unknown"))
        requested_units = max(int(request.get("qty", 0)), 0)
        fulfilled_units = max(int(request.get("fulfilled_units", 0)), 0)
        key = (region, blood_group, component, request_date)
        grouped[key]["requested_units"] += requested_units
        grouped[key]["fulfilled_units"] += min(fulfilled_units, requested_units)
        dimensions.add((region, blood_group, component))
        observed_dates.add(request_date)

    if not dimensions:
        return []
    first_date = start_date or min(observed_dates)
    last_date = end_date or max(observed_dates)
    rows: list[DemandHistoryRow] = []
    current = first_date
    while current <= last_date:
        for region, blood_group, component in sorted(dimensions):
            values = grouped[(region, blood_group, component, current)]
            requested = values["requested_units"]
            fulfilled = values["fulfilled_units"]
            rows.append(DemandHistoryRow(
                demand_date=current,
                region=region,
                hospital_id="ALL",
                blood_group=blood_group,
                component=component,
                requested_units=requested,
                fulfilled_units=fulfilled,
                shortage_units=max(requested - fulfilled, 0),
            ))
        current += timedelta(days=1)
    return rows


class InMemoryForecastRepository:
    def __init__(self, results: Iterable[ForecastResult] = ()) -> None:
        self.results = list(results)

    def get_forecast(self, region: str, horizon_days: int) -> ForecastResult | None:
        validate_horizon_days(horizon_days)
        # Serve the newest published run even after its target horizon has
        # passed. Forecast production and API availability are separate
        # concerns; a delayed batch must not turn the dashboard blank.
        for result in sorted(self.results, key=lambda item: item.created_at, reverse=True):
            regional_points = [point for point in result.points if point.region == region]
            target_dates = sorted({point.target_date for point in regional_points})[:horizon_days]
            points = [point for point in regional_points if point.target_date in target_dates]
            if points:
                return result.model_copy(update={"points": points})
        return None


class BigQueryForecastRepository:
    def __init__(self, project_id: str, dataset_id: str = "bloodnet") -> None:
        self.project_id = project_id
        self.dataset_id = dataset_id

    def get_forecast(self, region: str, horizon_days: int) -> ForecastResult | None:
        validate_horizon_days(horizon_days)
        from google.cloud import bigquery

        client = bigquery.Client(project=self.project_id)
        query = f"""
            WITH latest_run AS (
              SELECT forecast_run_id
              FROM `{self.project_id}.{self.dataset_id}.forecast_results`
              WHERE region = @region
              ORDER BY created_at DESC, forecast_run_id DESC
              LIMIT 1
            ), latest_dates AS (
              SELECT DISTINCT target_date
              FROM `{self.project_id}.{self.dataset_id}.forecast_results`
              WHERE region = @region
                AND forecast_run_id = (SELECT forecast_run_id FROM latest_run)
              ORDER BY target_date
              LIMIT @horizon_days
            )
            SELECT forecast_run_id, model_version, confidence_level, created_at,
                   target_date, region, blood_group, component,
                   predicted_demand, lower_bound, upper_bound,
                   projected_supply, shortage_probability
            FROM `{self.project_id}.{self.dataset_id}.forecast_results`
            WHERE region = @region
              AND forecast_run_id = (SELECT forecast_run_id FROM latest_run)
              AND target_date IN (SELECT target_date FROM latest_dates)
            ORDER BY target_date, blood_group, component
        """
        config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("region", "STRING", region),
            bigquery.ScalarQueryParameter("horizon_days", "INT64", horizon_days),
        ])
        rows = list(client.query(query, job_config=config).result())
        if not rows:
            return None
        first = rows[0]
        return ForecastResult(
            forecast_run_id=str(first["forecast_run_id"]),
            model_version=str(first["model_version"]),
            confidence_level=float(first["confidence_level"]),
            created_at=first["created_at"],
            points=[ForecastPoint(**dict(row)) for row in rows],
        )


def build_forecast_repository() -> ForecastRepository:
    project_id = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT")
    backend = os.getenv("BLOODNET_FORECAST_BACKEND", "bigquery" if project_id else "memory").lower()
    if backend == "bigquery":
        if not project_id:
            raise RuntimeError("GCP_PROJECT_ID is required for the BigQuery forecast repository")
        return BigQueryForecastRepository(project_id, os.getenv("BLOODNET_BIGQUERY_DATASET", "bloodnet"))
    if backend != "memory":
        raise RuntimeError("BLOODNET_FORECAST_BACKEND must be 'bigquery' or 'memory'")
    return InMemoryForecastRepository()


def forecast_response(result: ForecastResult | None, region: str, horizon_days: int) -> dict[str, Any]:
    validate_horizon_days(horizon_days)
    if result is None or not result.points:
        return {
            "region": region,
            "horizon_days": horizon_days,
            "forecast": [],
            "status": "no_forecast_available",
        }
    latest_target_date = max(point.target_date for point in result.points)
    return {
        "region": region,
        "horizon_days": horizon_days,
        "forecast_run_id": result.forecast_run_id,
        "model_version": result.model_version,
        "confidence_level": result.confidence_level,
        "created_at": result.created_at.isoformat(),
        "latest_target_date": latest_target_date.isoformat(),
        "is_stale": forecast_is_stale(result),
        "forecast": [_forecast_point_response(point, result.confidence_level) for point in result.points],
        "status": "available",
    }


def apply_live_inventory_supply(
    result: ForecastResult,
    inventory_units: Iterable[Any],
    bank_ids: set[str],
) -> ForecastResult:
    """Use scoped, available PostgreSQL inventory for every forecast date."""
    if not bank_ids:
        # An unresolved scope is not the same as an empty inventory. Preserve
        # the supply published with the forecast instead of manufacturing a
        # 100% shortage signal for every product.
        return result
    units = [
        unit for unit in inventory_units
        if unit.bank_id in bank_ids
        and getattr(unit.status, "value", unit.status) == "available"
    ]
    if not units:
        # A resolved organization scope can still use different identifiers
        # from inventory rows. Do not replace a valid published projection
        # with zero until at least one scoped live unit is actually matched.
        return result
    points = []
    for point in result.points:
        projected_supply = sum(
            1 for unit in units
            if getattr(unit.group, "value", unit.group) == point.blood_group
            and (
                point.component in {None, "ALL"}
                or getattr(unit.component, "value", unit.component) == point.component
            )
            and unit.expires_at.date() >= point.target_date
        )
        points.append(point.model_copy(update={"projected_supply": Decimal(projected_supply)}))
    return result.model_copy(update={"points": points})


def _forecast_point_response(point: ForecastPoint, confidence_level: float) -> dict[str, Any]:
    interval_z = 1.96 if confidence_level >= 0.95 else 1.645
    sigma = max(float(point.upper_bound - point.lower_bound) / (2 * interval_z), 0.0)
    mean = float(point.predicted_demand)
    supply = float(point.projected_supply)
    if sigma == 0:
        probability = 1.0 if mean > supply else 0.0
    else:
        cdf = 0.5 * (1 + erf((supply - mean) / (sigma * sqrt(2))))
        probability = max(0.0, min(1.0, 1.0 - cdf))
    value = point.model_dump(mode="json", exclude={"component"})
    for field in ("predicted_demand", "lower_bound", "upper_bound", "projected_supply"):
        value[field] = float(getattr(point, field))
    value["shortage_probability"] = round(probability, 6)
    return value
