"""Contracts shared by the Blood Weather pipeline and API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class DemandHistoryRow(BaseModel):
    """One normalized daily demand observation."""

    demand_date: date
    region: str = Field(min_length=1)
    hospital_id: str = Field(min_length=1)
    blood_group: str = Field(min_length=1)
    component: str = Field(min_length=1)
    requested_units: int = Field(ge=0)
    fulfilled_units: int = Field(ge=0)
    shortage_units: int = Field(ge=0)


class ForecastPoint(BaseModel):
    """A blood-group planning forecast for one target day.

    Component remains optional for reading legacy persisted rows, but it is
    not part of the Blood Weather response contract.
    """

    target_date: date
    region: str
    blood_group: str
    component: str | None = None
    predicted_demand: Decimal = Field(ge=0)
    lower_bound: Decimal = Field(ge=0)
    upper_bound: Decimal = Field(ge=0)
    projected_supply: Decimal = Field(default=Decimal("0"), ge=0)
    shortage_probability: float = Field(default=1.0, ge=0, le=1)


class ForecastResult(BaseModel):
    """Persisted result metadata and its forecast points."""

    forecast_run_id: str
    model_version: str
    confidence_level: float = Field(gt=0, lt=1)
    created_at: datetime
    points: list[ForecastPoint]
