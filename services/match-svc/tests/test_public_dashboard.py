from public_dashboard import (
    _organization_maps,
    build_public_dashboard,
    public_demo_scenarios,
    run_public_demo_scenario,
)
from types import SimpleNamespace


def test_landing_dashboard_only_uses_cluster_for_city_aggregation():
    organizations = [SimpleNamespace(
        id="BANK-1",
        type=SimpleNamespace(value="blood_bank"),
        metadata={"city": "Pune", "region_id": "Pune", "zone": "West"},
    )]

    bank_regions, _, banks = _organization_maps(organizations)

    assert bank_regions == {"BANK-1": "west"}
    assert banks["west"] == 1


def test_public_dashboard_does_not_invent_operational_fallback_data():
    payload = build_public_dashboard(None, None, None, "pune")

    assert payload["selected_region"] == "pune"
    assert payload["selected_region_name"] == "Pune"
    assert payload["data_mode"] == "unavailable"
    assert "No live operational data" in payload["data_note"]
    assert payload["regions"]
    assert all(item["data_mode"] == "unavailable" for item in payload["regions"])
    assert payload["availability"] == []
    assert payload["forecast"] == []
    assert payload["activity"] == []
    assert payload["overview"]["total_units"] == 0


def test_all_network_dashboard_does_not_query_forecast_per_region():
    class ExplodingForecastRepository:
        def get_forecast(self, region, horizon_days):
            raise AssertionError("all-network view must not fan out forecast queries")

    payload = build_public_dashboard(None, ExplodingForecastRepository(), None, "all")

    assert payload["selected_region"] == "all"
    assert payload["forecast"] == []


def test_public_scenarios_are_read_only_deterministic_projections():
    scenarios = public_demo_scenarios("mumbai")
    assert all(scenario["region"] == "Mumbai" for scenario in scenarios)

    inventory_first = run_public_demo_scenario("inventory-first", "mumbai")
    assert inventory_first["simulation"] is True
    assert inventory_first["outcome"]["donor_units"] == 0
    assert inventory_first["outcome"]["secured_units"] == 2
