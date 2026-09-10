from datetime import datetime, timedelta, timezone

from contracts.models import BloodBank, BloodGroup, Component, GeoPoint, InventoryStatus, InventoryUnit
from transfer_optimizer import recommend_transfer


def _bank(bank_id, reserve=1):
    return BloodBank(bank_id=bank_id, name=bank_id, geo=GeoPoint(lat=1, lng=1), licence_id=bank_id,
                     min_reserve={"O+|RBC": reserve})


def _unit(unit_id, bank_id, days=20):
    now = datetime.now(timezone.utc)
    return InventoryUnit(unit_id=unit_id, bank_id=bank_id, group=BloodGroup.O_POS,
                         component=Component.RBC, collected_at=now - timedelta(days=2),
                         expires_at=now + timedelta(days=days), status=InventoryStatus.AVAILABLE)


def test_transfer_recommendation_preserves_reserve_and_does_not_mutate():
    units = [_unit("U1", "B1"), _unit("U2", "B1"), _unit("U3", "B1")]
    recommendation = recommend_transfer(units=units, banks=[_bank("B1"), _bank("B2")],
                                        destination_bank_id="B2", group="O+", component=Component.RBC,
                                        quantity=2)
    assert recommendation is not None
    assert recommendation.type == "TRANSFER_INVENTORY"
    assert recommendation.state == "AWAITING_APPROVAL"
    assert recommendation.payload["unit_ids"] == ["U1", "U2"]
    assert all(unit.status == InventoryStatus.AVAILABLE for unit in units)


def test_transfer_returns_none_when_reserve_would_be_breached():
    assert recommend_transfer(units=[_unit("U1", "B1")], banks=[_bank("B1"), _bank("B2")],
                              destination_bank_id="B2", group="O+", component=Component.RBC,
                              quantity=1) is None
