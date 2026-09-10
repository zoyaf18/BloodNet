from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from contracts.forecast import ForecastPoint, ForecastResult
from contracts.models import BloodGroup, Component, InventoryStatus, InventoryUnit
from forecast_service import InMemoryForecastRepository, apply_live_inventory_supply, build_demand_history, forecast_response


def test_build_demand_history_fills_zero_demand_days():
    rows = [
        {
            "created_at": datetime(2026, 8, 20, tzinfo=timezone.utc),
            "payload": {"request": {"region": "Pune", "hospital_id": "H1", "group": "A+", "component": "RBC", "qty": 3}},
        },
        {
            "created_at": datetime(2026, 8, 22, tzinfo=timezone.utc),
            "payload": {"request": {"region": "Pune", "hospital_id": "H1", "group": "A+", "component": "RBC", "qty": 2}},
        },
    ]

    history = build_demand_history(rows)

    assert [(item.demand_date, item.requested_units) for item in history] == [
        (date(2026, 8, 20), 3),
        (date(2026, 8, 21), 0),
        (date(2026, 8, 22), 2),
    ]


def test_forecast_repository_filters_region_and_horizon():
    points = [
        ForecastPoint(target_date=date.today(), region="Pune", blood_group="A+", component="RBC", predicted_demand=Decimal(1), lower_bound=Decimal(1), upper_bound=Decimal(2)),
        ForecastPoint(target_date=date.today(), region="Pune", blood_group="O-", component="FFP", predicted_demand=Decimal(2), lower_bound=Decimal(1), upper_bound=Decimal(3)),
        ForecastPoint(target_date=date.today() + timedelta(days=1), region="Pune", blood_group="A+", component="RBC", predicted_demand=Decimal(3), lower_bound=Decimal(2), upper_bound=Decimal(4)),
    ]
    result = ForecastResult(
        forecast_run_id="run-1",
        model_version="arima_plus_xreg_v1",
        confidence_level=0.95,
        created_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        points=points,
    )

    filtered = InMemoryForecastRepository([result]).get_forecast("Pune", 2)

    assert filtered is not None
    assert len(filtered.points) == 3
    assert InMemoryForecastRepository([result]).get_forecast("Mumbai", 7) is None


def test_forecast_response_hides_component_from_blood_weather_contract():
    result = ForecastResult(
        forecast_run_id="blood-group-run",
        model_version="test",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc),
        points=[ForecastPoint(
            target_date=date.today(), region="Pune", blood_group="O-", component="RBC",
            predicted_demand=Decimal(8), lower_bound=Decimal(6), upper_bound=Decimal(10),
        )],
    )

    point = forecast_response(result, "Pune", 1)["forecast"][0]

    assert point["blood_group"] == "O-"
    assert point["predicted_demand"] == 8
    assert "component" not in point


def test_empty_forecast_is_explicitly_unavailable():
    response = forecast_response(None, "Pune", 7)

    assert response == {
        "region": "Pune",
        "horizon_days": 7,
        "forecast": [],
        "status": "no_forecast_available",
    }


def test_repository_returns_latest_available_run_when_horizon_has_passed():
    stale_date = date.today() - timedelta(days=2)
    older = ForecastResult(
        forecast_run_id="older-run",
        model_version="test",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        points=[ForecastPoint(target_date=stale_date, region="Pune", blood_group="A+", component="RBC", predicted_demand=Decimal(1), lower_bound=Decimal(0), upper_bound=Decimal(2))],
    )
    latest = older.model_copy(update={
        "forecast_run_id": "latest-run",
        "created_at": datetime.now(timezone.utc) - timedelta(days=1),
        "points": [ForecastPoint(target_date=stale_date + timedelta(days=1), region="Pune", blood_group="O-", component="RBC", predicted_demand=Decimal(2), lower_bound=Decimal(1), upper_bound=Decimal(3))],
    })

    result = InMemoryForecastRepository([older, latest]).get_forecast("Pune", 7)

    assert result is not None
    assert result.forecast_run_id == "latest-run"
    response = forecast_response(result, "Pune", 7)
    assert response["is_stale"] is True
    assert response["latest_target_date"] == (stale_date + timedelta(days=1)).isoformat()


def test_shortage_probability_uses_supply_and_prediction_interval():
    result = ForecastResult(
        forecast_run_id="run-2",
        model_version="arima_plus_xreg_v1",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc),
        points=[ForecastPoint(
            target_date=date.today(), region="Pune", blood_group="A+", component="RBC",
            predicted_demand=Decimal("10"), lower_bound=Decimal("6"), upper_bound=Decimal("14"),
            projected_supply=Decimal("20"),
        )],
    )

    point = forecast_response(result, "Pune", 1)["forecast"][0]

    assert point["shortage_probability"] < 0.01


def test_live_inventory_replaces_stale_projected_supply():
    result = ForecastResult(
        forecast_run_id="run-live-supply",
        model_version="test",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc),
        points=[ForecastPoint(
            target_date=date.today(), region="Pune", blood_group="A+", component="RBC",
            predicted_demand=Decimal(1), lower_bound=Decimal(1), upper_bound=Decimal(1),
            projected_supply=Decimal(0),
        )],
    )
    unit = InventoryUnit(
        unit_id="UNIT-1", bank_id="BANK-1", group=BloodGroup.A_POS,
        component=Component.RBC, collected_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=2),
        status=InventoryStatus.AVAILABLE,
    )

    updated = apply_live_inventory_supply(result, [unit], {"BANK-1"})

    assert updated.points[0].projected_supply == 1
    assert forecast_response(updated, "Pune", 1)["forecast"][0]["shortage_probability"] == 0


def test_unresolved_live_scope_preserves_published_supply():
    result = ForecastResult(
        forecast_run_id="run-published-supply",
        model_version="test",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc),
        points=[ForecastPoint(
            target_date=date.today(), region="Pune", blood_group="A+", component="RBC",
            predicted_demand=Decimal(10), lower_bound=Decimal(6), upper_bound=Decimal(14),
            projected_supply=Decimal(20),
        )],
    )

    updated = apply_live_inventory_supply(result, [], set())

    assert updated.points[0].projected_supply == 20
    assert forecast_response(updated, "Pune", 1)["forecast"][0]["shortage_probability"] < 0.01


def test_unmatched_live_inventory_scope_preserves_published_supply():
    result = ForecastResult(
        forecast_run_id="run-mismatched-supply-scope",
        model_version="test",
        confidence_level=0.95,
        created_at=datetime.now(timezone.utc),
        points=[ForecastPoint(
            target_date=date.today(), region="Pune", blood_group="B+", component="RBC",
            predicted_demand=Decimal(10), lower_bound=Decimal(6), upper_bound=Decimal(14),
            projected_supply=Decimal(20),
        )],
    )

    updated = apply_live_inventory_supply(result, [], {"BANK-ID-FORMAT-MISMATCH"})

    assert updated.points[0].projected_supply == 20
