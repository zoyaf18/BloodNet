from datetime import datetime, timedelta, timezone

from contracts.models import (
    BloodBank,
    BloodGroup,
    Component,
    GeoPoint,
    InventoryStatus,
    InventoryUnit,
)
from graph_service import DonorEdge, NetworkGraph, SupplyEdge, TransferEdge
from transfer_optimizer import recommend_transfer


def test_graph_supports_s6_analysis_modes():
    graph = NetworkGraph(
        supply_edges=[
            SupplyEdge("B1", "H1", 75.0),
            SupplyEdge("B2", "H1", 25.0),
        ],
        transfer_edges=[
            TransferEdge("B1", "B2", 3.0),
            TransferEdge("B2", "B3", 5.0),
        ],
        donor_edges=[
            DonorEdge("D1", "B1", "north", "O+"),
            DonorEdge("D2", "B2", "north", "O+"),
            DonorEdge("D3", "B3", "south", "O+"),
        ],
        demand_by_region={"north": 20.0, "south": 5.0},
    )

    dependency = graph.analyze("dependency_analysis", {"hospital_id": "H1"})
    assert dependency["hospital_id"] == "H1"
    assert dependency["supplier_count"] == 2

    weak = graph.analyze("weak_coverage_zones")
    assert any(zone["region"] == "north" for zone in weak["zones"])
    assert all("coverage_ratio" in zone for zone in weak["zones"])

    camp = graph.analyze("camp_placement", {"demand_by_region": {"north": 20.0, "south": 5.0}})
    assert camp["selected_region"] in {"north", "south"}
    assert camp["coverage_score"] >= 0.0

    cascade = graph.analyze("cascade_simulation", {"unavailable_bank_id": "B1", "max_hours": 4})
    assert "reachable_hospitals" in cascade
    assert isinstance(cascade["reachable_hospitals"], list)


def test_transfer_optimizer_honors_reserve_and_route_constraints():
    now = datetime.now(timezone.utc)
    bank_a = BloodBank(
        bank_id="B1",
        name="Bank A",
        geo=GeoPoint(lat=1.0, lng=1.0),
        licence_id="LIC-A",
        min_reserve={"O+|RBC": 1},
    )
    bank_b = BloodBank(
        bank_id="B2",
        name="Bank B",
        geo=GeoPoint(lat=1.5, lng=1.5),
        licence_id="LIC-B",
        min_reserve={"O+|RBC": 0},
    )
    units = [
        InventoryUnit(
            unit_id="U1",
            bank_id="B1",
            group=BloodGroup.O_POS,
            component=Component.RBC,
            collected_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=6),
            status=InventoryStatus.AVAILABLE,
        ),
        InventoryUnit(
            unit_id="U2",
            bank_id="B1",
            group=BloodGroup.O_POS,
            component=Component.RBC,
            collected_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=8),
            status=InventoryStatus.AVAILABLE,
        ),
    ]

    recommendation = recommend_transfer(
        units=units,
        banks=[bank_a, bank_b],
        destination_bank_id="B2",
        group="O+",
        component=Component.RBC,
        quantity=1,
        lead_time_hours=4.0,
        cold_chain_buffer_hours=2.0,
        route_constraints={"max_transfer_hours": 4.0, "max_distance_km": 500.0},
    )

    assert recommendation is not None
    assert recommendation.payload["from_bank"] == "B1"
    assert recommendation.payload["unit_ids"]
    assert all(unit.status == InventoryStatus.AVAILABLE for unit in units)
