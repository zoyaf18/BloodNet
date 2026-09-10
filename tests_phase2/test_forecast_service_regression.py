from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
MATCH_SVC = ROOT / "services" / "match-svc"
for path in (ROOT, MATCH_SVC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from contracts.forecast import ForecastPoint, ForecastResult
from forecast_service import (
    InMemoryForecastRepository,
    forecast_response,
    validate_horizon_days,
)


def make_result() -> ForecastResult:
    return ForecastResult(
        forecast_run_id="RUN-1",
        model_version="test",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc),
        points=[ForecastPoint(
            target_date=date.today(),
            region="Pune",
            blood_group="O-",
            component="RBC",
            predicted_demand=Decimal("10"),
            lower_bound=Decimal("8"),
            upper_bound=Decimal("12"),
            projected_supply=Decimal("6"),
        )],
    )


@pytest.mark.parametrize("horizon_days", [0, 15, -1])
def test_forecast_horizon_is_limited_to_one_through_fourteen_days(horizon_days):
    with pytest.raises(ValueError, match="between 1 and 14"):
        validate_horizon_days(horizon_days)


def test_forecast_response_exposes_shortage_probability():
    result = InMemoryForecastRepository([make_result()]).get_forecast("Pune", 7)

    response = forecast_response(result, "Pune", 7)

    assert response["status"] == "available"
    assert response["forecast"][0]["shortage_probability"] > 0.5