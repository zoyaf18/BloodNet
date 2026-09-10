from importlib.util import module_from_spec, spec_from_file_location
from collections import Counter
from pathlib import Path


def _seed_module():
    path = Path(__file__).parents[1] / "scripts" / "seed_synthetic_india.py"
    spec = spec_from_file_location("seed_synthetic_india", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_uses_facility_city_as_the_operational_region():
    seed = _seed_module()
    organizations, metadata = seed.build_organizations(20)
    _, _, _, donors = seed.build_users(20, metadata)

    _, inventory, requests, cases, projections, demand = seed.build_domain_rows(
        20, metadata, donors
    )
    organizations_by_id = {str(row[0]): item for row, item in zip(organizations, metadata)}

    for unit_row in inventory:
        payload = unit_row[1].obj
        bank = organizations_by_id[payload["bank_id"]]
        assert payload["region"] == payload["city"] == bank["city"]
        assert payload["zone"] == bank["region"]

    requests_by_id = {}
    for request_row in requests:
        payload = request_row[1].obj
        hospital = organizations_by_id[payload["hospital_id"]]
        assert payload["region"] == payload["city"] == hospital["city"]
        assert payload["zone"] == hospital["region"]
        requests_by_id[payload["request_id"]] = payload

    for case_row, projection_row in zip(cases, projections):
        case_payload = case_row[1].obj
        request = requests_by_id[case_payload["request_id"]]
        assert case_payload["region"] == request["region"]
        assert case_payload["units_from_inventory"] + case_payload["units_from_donors_remaining"] == request["qty"]
        assert request["status"] == ("fulfilled" if case_payload["units_from_donors_remaining"] == 0 else "pending")
        assert case_payload["outcome"] == ("fulfilled" if case_payload["units_from_donors_remaining"] == 0 else "pending")
        assert case_payload["reservation_state"] == ("reserved" if case_payload["units_from_inventory"] > 0 else "not_proposed")
        ranked_donors = projection_row[1].obj
        assert isinstance(ranked_donors, list)
        assert ranked_donors[0]["donor_id"].startswith("SYNTH-DONOR-")
        assert projection_row[2].obj["region"] == request["region"]
        expected_status = "pending" if case_payload["units_from_donors_remaining"] > 0 else "inventory_covered"
        assert projection_row[2].obj["status"] == expected_status

    assert all(row[1] in {item["city"] for item in metadata} for row in demand)


def test_seed_distributes_uneven_activity_across_all_city_clusters():
    seed = _seed_module()
    organizations, metadata = seed.build_organizations(500)
    _, _, _, donors = seed.build_users(500, metadata)

    _, inventory, requests, _, _, _ = seed.build_domain_rows(2_000, metadata, donors)
    expected_cities = {city for cities in seed.REGIONS.values() for city in cities}
    inventory_counts = Counter(row[1].obj["city"] for row in inventory)
    request_counts = Counter(row[1].obj["city"] for row in requests)

    assert expected_cities <= set(inventory_counts)
    assert expected_cities <= set(request_counts)
    assert max(inventory_counts.values()) > min(inventory_counts.values())
    assert max(request_counts.values()) > min(request_counts.values())
    assert len({row[1].obj["status"] for row in inventory}) == 3
