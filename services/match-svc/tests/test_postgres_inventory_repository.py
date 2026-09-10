from datetime import timezone

from postgres_inventory_repository import _inventory_unit_from_payload


def test_legacy_date_only_inventory_timestamps_are_normalized_to_utc():
    unit = _inventory_unit_from_payload({
        "unit_id": "UNIT-LEGACY",
        "bank_id": "BANK-PUNE",
        "group": "O+",
        "component": "RBC",
        "status": "available",
        "expires_at": "2026-09-30",
    })

    assert unit.expires_at.tzinfo == timezone.utc
    assert unit.collected_at.tzinfo == timezone.utc
