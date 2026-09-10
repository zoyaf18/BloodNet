from datetime import datetime, timedelta

from inventory_match import find_inventory_matches
from contracts.models import (
    BloodBank,
    BloodGroup,
    Component,
    GeoPoint,
    Hospital,
    InventoryStatus,
    InventoryUnit,
    Request,
    RequestStatus,
    Urgency,
    VerificationState,
)

NOW = datetime(2026, 8, 22, 12, 0, 0)
HOSPITAL_GEO = GeoPoint(lat=18.5204, lng=73.8567)  # Pune

HOSPITAL = Hospital(
    hospital_id="H1", name="Ruby Hall", geo=HOSPITAL_GEO, tier="tier1"
)


def make_request(**overrides) -> Request:
    defaults = dict(
        request_id="R1",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        qty=5,
        hospital_id="H1",
        urgency=Urgency.CRITICAL,
        required_by=NOW + timedelta(hours=4),
        source_channel="whatsapp",
        verification_state=VerificationState.VERIFIED,
        status=RequestStatus.OPEN,
    )
    defaults.update(overrides)
    return Request(**defaults)


def make_bank(bank_id: str, lat: float, lng: float) -> BloodBank:
    return BloodBank(
        bank_id=bank_id, name=bank_id, geo=GeoPoint(lat=lat, lng=lng), licence_id="LIC1"
    )


def make_unit(unit_id, bank_id, group, component=Component.RBC, days_to_expiry=10, status=InventoryStatus.AVAILABLE):
    return InventoryUnit(
        unit_id=unit_id,
        bank_id=bank_id,
        group=group,
        component=component,
        collected_at=NOW - timedelta(days=5),
        expires_at=NOW + timedelta(days=days_to_expiry),
        status=status,
    )


def test_finds_and_ranks_nearby_banks_nearest_first():
    near_bank = make_bank("BANK_NEAR", 18.53, 73.86)   # ~1-2km from hospital
    far_bank = make_bank("BANK_FAR", 19.0, 74.5)        # much further
    units = [
        make_unit("U1", "BANK_FAR", BloodGroup.A_POS),
        make_unit("U2", "BANK_NEAR", BloodGroup.A_POS),
    ]
    result = find_inventory_matches(make_request(), HOSPITAL, [near_bank, far_bank], units)
    assert [m.bank_id for m in result.matches] == ["BANK_NEAR", "BANK_FAR"]
    assert result.matches[0].distance_km < result.matches[1].distance_km


def test_allocates_up_to_requested_quantity_and_reports_remaining():
    bank = make_bank("BANK_A", 18.53, 73.86)
    units = [make_unit(f"U{i}", "BANK_A", BloodGroup.A_POS) for i in range(3)]
    result = find_inventory_matches(make_request(qty=5), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 3
    assert result.units_remaining == 2  # goes to donor mobilization


def test_full_coverage_leaves_zero_remaining():
    bank = make_bank("BANK_A", 18.53, 73.86)
    units = [make_unit(f"U{i}", "BANK_A", BloodGroup.A_POS) for i in range(8)]
    result = find_inventory_matches(make_request(qty=5), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 5
    assert result.units_remaining == 0


def test_incompatible_group_excluded():
    bank = make_bank("BANK_A", 18.53, 73.86)
    units = [make_unit("U1", "BANK_A", BloodGroup.B_POS)]  # not compatible with A+ recipient
    result = find_inventory_matches(make_request(group=BloodGroup.A_POS), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 0
    assert result.matches == []


def test_o_neg_donor_unit_is_compatible_with_any_recipient():
    bank = make_bank("BANK_A", 18.53, 73.86)
    units = [make_unit("U1", "BANK_A", BloodGroup.O_NEG)]
    result = find_inventory_matches(make_request(group=BloodGroup.AB_POS), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 1


def test_reserved_and_issued_units_excluded():
    bank = make_bank("BANK_A", 18.53, 73.86)
    units = [
        make_unit("U1", "BANK_A", BloodGroup.A_POS, status=InventoryStatus.RESERVED),
        make_unit("U2", "BANK_A", BloodGroup.A_POS, status=InventoryStatus.ISSUED),
    ]
    result = find_inventory_matches(make_request(), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 0


def test_units_expiring_before_cold_chain_buffer_excluded():
    bank = make_bank("BANK_A", 18.53, 73.86)
    # required_by is 4h from now; unit expiring in 30 minutes fails the
    # 1-day cold-chain buffer even though it's technically still "available"
    units = [make_unit("U1", "BANK_A", BloodGroup.A_POS, days_to_expiry=0)]
    result = find_inventory_matches(make_request(), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 0


def test_wrong_component_excluded():
    bank = make_bank("BANK_A", 18.53, 73.86)
    units = [make_unit("U1", "BANK_A", BloodGroup.A_POS, component=Component.PLATELETS_RDP)]
    result = find_inventory_matches(make_request(component=Component.RBC), HOSPITAL, [bank], units)
    assert result.units_from_inventory == 0


def test_no_banks_in_range_returns_empty_matches():
    result = find_inventory_matches(make_request(), HOSPITAL, [], [])
    assert result.matches == []
    assert result.units_from_inventory == 0
    assert result.units_remaining == 5
