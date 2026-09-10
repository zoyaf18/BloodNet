"""Deterministic, clearly labeled synthetic demand history for MVP validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
import random
from typing import Any

DEFAULT_GROUPS = ("O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-")
DEFAULT_LOCATIONS = {
    "Pune": "SYNTHETIC-PUNE-HOSPITAL",
    "Mumbai": "SYNTHETIC-MUMBAI-HOSPITAL",
    "Nashik": "SYNTHETIC-NASHIK-HOSPITAL",
    "Nagpur": "SYNTHETIC-NAGPUR-HOSPITAL",
}
FESTIVALS = ((1, 14), (3, 25), (8, 15), (10, 2), (10, 20), (12, 25))


@dataclass(frozen=True)
class SyntheticObservation:
    demand_date: date
    region: str
    hospital_id: str
    blood_group: str
    requested_units: int
    fulfilled_units: int
    shortage_units: int

    def as_dict(self) -> dict[str, Any]:
        return {
            field: value.isoformat() if isinstance(value := getattr(self, field), date) else value
            for field in self.__dataclass_fields__
        }


def _festival_multiplier(day: date) -> float:
    return 1.28 if (day.month, day.day) in FESTIVALS else 1.0


def _demand_units(rng: random.Random, day: date, group_index: int, location_index: int) -> int:
    weekly = (1.16, 0.93, 0.97, 1.00, 1.08, 1.22, 1.10)[day.weekday()]
    seasonal = 1.0 + 0.14 * math.cos((day.timetuple().tm_yday - 20) * 2 * math.pi / 365.25)
    baseline = (8.0 - group_index * 0.55) * (1.0 + location_index * 0.08)
    trend = 1.0 + 0.00025 * (day - date(2023, 1, 1)).days
    emergency = rng.uniform(2.2, 4.0) if rng.random() < 0.006 else 1.0
    return max(0, int(round(baseline * weekly * seasonal * _festival_multiplier(day) * trend * emergency * (1 + rng.gauss(0, 0.10)))))


def generate_synthetic_history(*, end_date: date | None = None, years: int = 3, holdout_days: int = 28, seed: int = 2026, locations: dict[str, str] | None = None, blood_groups: tuple[str, ...] = DEFAULT_GROUPS) -> tuple[list[SyntheticObservation], dict[str, Any]]:
    """Generate dense daily demand and return provenance plus holdout boundaries."""
    if years < 2:
        raise ValueError("years must be at least 2 for seasonal validation")
    if holdout_days < 7:
        raise ValueError("holdout_days must be at least 7")
    locations = locations or DEFAULT_LOCATIONS
    last_day = end_date or (date.today() - timedelta(days=1))
    first_day = last_day - timedelta(days=round(years * 365.25) - 1)
    holdout_start = last_day - timedelta(days=holdout_days - 1)
    rng = random.Random(seed)
    rows: list[SyntheticObservation] = []
    current = first_day
    while current <= last_day:
        for location_index, (region, hospital_id) in enumerate(sorted(locations.items())):
            for group_index, blood_group in enumerate(blood_groups):
                requested = _demand_units(rng, current, group_index, location_index)
                fulfilled = max(0, requested - (1 if rng.random() < 0.08 else 0))
                rows.append(SyntheticObservation(current, region, hospital_id, blood_group, requested, fulfilled, requested - fulfilled))
        current += timedelta(days=1)
    metadata = {
        "dataset_label": "synthetic_mvp", "source_type": "synthetic", "generator_version": "synthetic_daily_v1", "seed": seed,
        "first_date": first_day.isoformat(), "last_date": last_day.isoformat(), "holdout_start": holdout_start.isoformat(), "holdout_end": last_day.isoformat(),
        "holdout_days": holdout_days, "locations": sorted(locations), "blood_groups": list(blood_groups),
    }
    return rows, metadata


def holdout_summary(rows: list[SyntheticObservation], metadata: dict[str, Any]) -> dict[str, Any]:
    """Report a seasonal-naive holdout baseline before BQML training."""
    holdout_start = date.fromisoformat(metadata["holdout_start"])
    train = [row for row in rows if row.demand_date < holdout_start]
    holdout = [row for row in rows if row.demand_date >= holdout_start]
    by_series: dict[tuple[str, str], list[SyntheticObservation]] = {}
    for row in train:
        by_series.setdefault((row.region, row.blood_group), []).append(row)
    errors = []
    for row in holdout:
        history = by_series[(row.region, row.blood_group)]
        prior = next(item for item in reversed(history) if item.demand_date.weekday() == row.demand_date.weekday())
        errors.append(abs(row.requested_units - prior.requested_units))
    return {"holdout_rows": len(holdout), "baseline_mae_units": round(sum(errors) / len(errors), 3)}