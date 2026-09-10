"""Regression coverage for the repository audit inventory findings."""
from datetime import datetime, timedelta, timezone

import pytest

import inventory_mutation as mutation
from contracts.models import InventoryUnit


@pytest.fixture
def clock(monkeypatch):
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(mutation, "_utc_now", lambda: now)
    return now


def unit(clock):
    return InventoryUnit(unit_id="AUDIT-UNIT", bank_id="BANK-1", group="AB-",
                         component="RBC", collected_at=clock - timedelta(days=10),
                         expires_at=clock + timedelta(days=1))


def reserve(repository, **kwargs):
    return mutation.reserve_inventory(repository, case_id="C", request_id="R",
                                      bank_id="BANK-1", unit_ids=["AUDIT-UNIT"], **kwargs)


@pytest.mark.parametrize("operation", ["reserve", "transfer", "consume"])
def test_expired_unit_cannot_be_allocated(clock, operation):
    stock = unit(clock)
    repository = mutation.InventoryRepository([stock])
    reservation = reserve(repository) if operation == "consume" else None
    stock.expires_at = clock
    before = stock.status
    with pytest.raises(mutation.InventoryMutationError, match="expired"):
        if operation == "reserve":
            reserve(repository)
        elif operation == "transfer":
            mutation.transfer_inventory(repository, transfer_id="T", from_bank="BANK-1",
                                        to_bank="BANK-2", unit_ids=[stock.unit_id], reason="test")
        else:
            mutation.consume_reservation(repository, reservation.reservation_id)
    assert repository.get_unit(stock.unit_id).status == before


def test_expired_reservation_cannot_be_consumed(clock):
    repository = mutation.InventoryRepository([unit(clock)])
    reservation = reserve(repository, expires_at=clock + timedelta(hours=1))
    reservation.expires_at = clock
    with pytest.raises(mutation.InvalidReservationStateError, match="expired"):
        mutation.consume_reservation(repository, reservation.reservation_id)


@pytest.mark.parametrize("operation", ["release", "expire", "receive"])
def test_returned_expired_stock_is_discarded(clock, operation):
    stock = unit(clock)
    repository = mutation.InventoryRepository([stock])
    if operation == "receive":
        mutation.transfer_inventory(repository, transfer_id="T", from_bank="BANK-1",
                                    to_bank="BANK-2", unit_ids=[stock.unit_id], reason="test")
    else:
        reservation = reserve(repository, expires_at=clock + timedelta(hours=1))
        reservation.expires_at = clock
    stock.expires_at = clock
    if operation == "receive":
        with pytest.raises(mutation.InventoryMutationError, match="expired"):
            mutation.receive_transfer(repository, "T")
        assert repository.get_unit(stock.unit_id).status.value == "in_transit"
        return
    elif operation == "release":
        mutation.release_reservation(repository, reservation.reservation_id)
    else:
        mutation.expire_reservation(repository, reservation.reservation_id, now=clock)
    assert repository.get_unit(stock.unit_id).status.value == "discarded"
