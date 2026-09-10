from graph_service import DonorEdge, NetworkGraph, SupplyEdge, TransferEdge


def test_supplier_hhi_and_coverage_are_deterministic():
    graph = NetworkGraph(
        supply_edges=[SupplyEdge("B1", "H1", 75), SupplyEdge("B2", "H1", 25)],
        donor_edges=[DonorEdge("D1", "B1", "north", "O+"), DonorEdge("D2", "B2", "south", "O+")],
        demand_by_region={"north": 4, "south": 8},
    )
    assert graph.supplier_concentration("H1")["hhi"] == 0.625
    assert graph.coverage_zones()[1]["coverage_ratio"] == 0.125


def test_cascade_excludes_removed_bank_and_respects_lead_time():
    graph = NetworkGraph(
        supply_edges=[SupplyEdge("B1", "H1", avg_lead_time_hours=2), SupplyEdge("B2", "H2", avg_lead_time_hours=8)],
        transfer_edges=[TransferEdge("B1", "B2", 3)],
    )
    result = graph.cascade_reachability("B1", 4)
    assert result["reachable_hospitals"] == []
    assert result["unreachable_hospitals"] == ["H1", "H2"]
    assert result["remaining_transfer_edges"] == 0


def test_network_briefing_identifies_critical_node_and_failure_blast_radius():
    graph = NetworkGraph(
        supply_edges=[
            SupplyEdge("B1", "H1", 6, 1, inventory_units=1, blood_group="O-", component="RBC", region="north"),
            SupplyEdge("B1", "H2", 4, 2, inventory_units=1, blood_group="O-", component="RBC", region="north"),
            SupplyEdge("B2", "H2", 2, 3, inventory_units=8, blood_group="O-", component="RBC", region="north"),
        ],
        transfer_edges=[TransferEdge("B2", "B1", 2)],
    )

    briefing = graph.network_briefing()
    impact = graph.bank_failure_impact("B1", blood_group="O-", component="RBC", max_hours=4)

    assert briefing["critical_nodes"][0]["bank_id"] == "B1"
    assert briefing["critical_nodes"][0]["region"] == "north"
    assert impact["vulnerable_hospitals"] == ["H1"]
    assert impact["alternatives"]["H2"][0]["bank_id"] == "B2"


def test_network_briefing_preserves_region_metadata():
    graph = NetworkGraph(
        supply_edges=[SupplyEdge("B1", "H1", region="Pune")],
    )

    assert graph.network_briefing()["critical_nodes"][0]["region"] == "Pune"


def test_redistribution_opportunity_is_advisory_and_approval_gated():
    graph = NetworkGraph(supply_edges=[
        SupplyEdge("SURPLUS", "H1", 1, inventory_units=10, blood_group="A+", component="RBC"),
        SupplyEdge("DEFICIT", "H2", 8, inventory_units=1, blood_group="A+", component="RBC"),
    ], transfer_edges=[TransferEdge("SURPLUS", "DEFICIT", 1.5)])

    opportunities = graph.redistribution_opportunities(reserve_units=2)["opportunities"]

    assert opportunities[0]["from_bank"] == "SURPLUS"
    assert opportunities[0]["to_bank"] == "DEFICIT"
    assert opportunities[0]["requires_approval"] is True
