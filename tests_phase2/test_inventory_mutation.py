"""
Tests for the BloodNet inventory mutation domain layer.

Covers:
    - Reservation creation
    - Exact unit selection
    - Bank ownership validation
    - Double-reservation protection
    - Atomic validation failure
    - Reservation idempotency
    - Consumption
    - Release
    - Expiry
    - Bulk expiry
    - Invalid state transitions
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from contracts.models import (
    BloodGroup,
    Component,
    InventoryReservationStatus,
    InventoryStatus,
    InventoryUnit,
)
import sys
from pathlib import Path

MATCH_SVC_DIR = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "match-svc"
)

if str(MATCH_SVC_DIR) not in sys.path:
    sys.path.insert(0, str(MATCH_SVC_DIR))

from inventory_mutation import (
    InventoryMutationError,
    InventoryRepository,
    InventoryUnitAlreadyReservedError,
    InventoryUnitNotReservableError,
    InvalidReservationStateError,
    consume_reservation,
    expire_due_reservations,
    expire_reservation,
    release_reservation,
    receive_transfer,
    reserve_inventory,
    transfer_inventory,
)
# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

BANK_ID = "BANK-001"
OTHER_BANK_ID = "BANK-002"
CASE_ID = "CASE-001"
REQUEST_ID = "REQ-001"

BASE_TIME = datetime(
    2026,
    8,
    22,
    12,
    0,
    0,
    tzinfo=timezone.utc,
)


def make_unit(
    unit_id: str,
    *,
    bank_id: str = BANK_ID,
    status: InventoryStatus = InventoryStatus.AVAILABLE,
) -> InventoryUnit:
    return InventoryUnit(
        unit_id=unit_id,
        bank_id=bank_id,
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=BASE_TIME - timedelta(days=10),
        expires_at=BASE_TIME + timedelta(days=32),
        status=status,
    )


def make_repository(
    *units: InventoryUnit,
) -> InventoryRepository:
    return InventoryRepository(units=list(units))


# ---------------------------------------------------------------------------
# Reservation creation
# ---------------------------------------------------------------------------


def test_reserve_inventory_creates_reservation():
    unit1 = make_unit("UNIT-001")
    unit2 = make_unit("UNIT-002")

    repository = make_repository(unit1, unit2)

    reservation = reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001", "UNIT-002"],
        expires_at=BASE_TIME + timedelta(minutes=15),
        reservation_id="RES-001",
    )

    assert reservation.reservation_id == "RES-001"
    assert reservation.case_id == CASE_ID
    assert reservation.request_id == REQUEST_ID
    assert reservation.bank_id == BANK_ID
    assert reservation.unit_ids == ["UNIT-001", "UNIT-002"]
    assert reservation.status == InventoryReservationStatus.RESERVED

    assert repository.get_unit("UNIT-001").status == InventoryStatus.RESERVED
    assert repository.get_unit("UNIT-002").status == InventoryStatus.RESERVED


def test_reservation_does_not_modify_unselected_units():
    selected = make_unit("UNIT-001")
    unselected = make_unit("UNIT-002")

    repository = make_repository(selected, unselected)

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
    )

    assert repository.get_unit("UNIT-001").status == InventoryStatus.RESERVED
    assert repository.get_unit("UNIT-002").status == InventoryStatus.AVAILABLE


def test_reservation_rejects_empty_unit_list():
    repository = make_repository(make_unit("UNIT-001"))

    with pytest.raises(InventoryMutationError):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=[],
        )


def test_reservation_rejects_duplicate_unit_ids():
    repository = make_repository(make_unit("UNIT-001"))

    with pytest.raises(InventoryMutationError):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=["UNIT-001", "UNIT-001"],
        )

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.AVAILABLE
    )


# ---------------------------------------------------------------------------
# Validation and atomicity
# ---------------------------------------------------------------------------


def test_reservation_rejects_wrong_bank():
    unit = make_unit(
        "UNIT-001",
        bank_id=OTHER_BANK_ID,
    )

    repository = make_repository(unit)

    with pytest.raises(InventoryMutationError):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=["UNIT-001"],
        )

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.AVAILABLE
    )


def test_reservation_rejects_non_available_unit():
    unit = make_unit(
        "UNIT-001",
        status=InventoryStatus.ISSUED,
    )

    repository = make_repository(unit)

    with pytest.raises(InventoryUnitNotReservableError):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=["UNIT-001"],
        )

    assert repository.get_unit("UNIT-001").status == InventoryStatus.ISSUED


def test_reservation_validation_is_atomic():
    """
    If one unit is invalid, none of the other units may be reserved.
    """

    valid = make_unit("UNIT-001")

    invalid = make_unit(
        "UNIT-002",
        status=InventoryStatus.ISSUED,
    )

    repository = make_repository(valid, invalid)

    with pytest.raises(InventoryUnitNotReservableError):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=["UNIT-001", "UNIT-002"],
        )

    assert repository.get_unit("UNIT-001").status == InventoryStatus.AVAILABLE
    assert repository.get_unit("UNIT-002").status == InventoryStatus.ISSUED


# ---------------------------------------------------------------------------
# Double reservation / idempotency
# ---------------------------------------------------------------------------


def test_same_unit_cannot_be_reserved_twice():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    with pytest.raises(InventoryUnitAlreadyReservedError):
        reserve_inventory(
            repository,
            case_id="CASE-002",
            request_id="REQ-002",
            bank_id=BANK_ID,
            unit_ids=["UNIT-001"],
            reservation_id="RES-002",
        )


def test_same_reservation_id_is_idempotent():
    repository = make_repository(make_unit("UNIT-001"))

    first = reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    second = reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    assert first.reservation_id == second.reservation_id
    assert first.status == second.status
    assert first.unit_ids == second.unit_ids
    assert len(repository.get_reservations()) == 1


def test_same_reservation_id_with_different_details_is_rejected():
    repository = make_repository(
        make_unit("UNIT-001"),
        make_unit("UNIT-002"),
    )

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    with pytest.raises(InventoryMutationError):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=["UNIT-002"],
            reservation_id="RES-001",
        )

    assert repository.get_unit("UNIT-001").status == InventoryStatus.RESERVED
    assert repository.get_unit("UNIT-002").status == InventoryStatus.AVAILABLE


# ---------------------------------------------------------------------------
# Consume
# ---------------------------------------------------------------------------


def test_consume_reservation_issues_units():
    repository = make_repository(
        make_unit("UNIT-001"),
        make_unit("UNIT-002"),
    )

    reservation = reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001", "UNIT-002"],
        reservation_id="RES-001",
    )

    consumed = consume_reservation(
        repository,
        reservation.reservation_id,
    )

    assert consumed.status == InventoryReservationStatus.CONSUMED
    assert consumed.consumed_at is not None

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.ISSUED
    )
    assert (
        repository.get_unit("UNIT-002").status
        == InventoryStatus.ISSUED
    )


def test_consume_is_idempotent():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    first = consume_reservation(repository, "RES-001")
    second = consume_reservation(repository, "RES-001")

    assert first.status == InventoryReservationStatus.CONSUMED
    assert second.status == InventoryReservationStatus.CONSUMED
    assert first.consumed_at == second.consumed_at

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.ISSUED
    )


def test_cannot_consume_released_reservation():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    release_reservation(repository, "RES-001")

    with pytest.raises(InvalidReservationStateError):
        consume_reservation(repository, "RES-001")


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def test_release_reservation_returns_units_to_available():
    repository = make_repository(
        make_unit("UNIT-001"),
        make_unit("UNIT-002"),
    )

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001", "UNIT-002"],
        reservation_id="RES-001",
    )

    released = release_reservation(
        repository,
        "RES-001",
    )

    assert released.status == InventoryReservationStatus.RELEASED
    assert released.released_at is not None

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.AVAILABLE
    )
    assert (
        repository.get_unit("UNIT-002").status
        == InventoryStatus.AVAILABLE
    )


def test_release_is_idempotent():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    first = release_reservation(repository, "RES-001")
    second = release_reservation(repository, "RES-001")

    assert first.status == InventoryReservationStatus.RELEASED
    assert second.status == InventoryReservationStatus.RELEASED
    assert first.released_at == second.released_at

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.AVAILABLE
    )


def test_cannot_release_consumed_reservation():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        reservation_id="RES-001",
    )

    consume_reservation(repository, "RES-001")

    with pytest.raises(InvalidReservationStateError):
        release_reservation(repository, "RES-001")

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.ISSUED
    )


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def test_expire_reservation_returns_units_to_available():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        expires_at=BASE_TIME + timedelta(minutes=15),
        reservation_id="RES-001",
    )

    expired = expire_reservation(
        repository,
        "RES-001",
        now=BASE_TIME + timedelta(minutes=16),
    )

    assert expired.status == InventoryReservationStatus.EXPIRED

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.AVAILABLE
    )


def test_reservation_cannot_expire_before_expiry_time():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        expires_at=BASE_TIME + timedelta(minutes=15),
        reservation_id="RES-001",
    )

    with pytest.raises(InvalidReservationStateError):
        expire_reservation(
            repository,
            "RES-001",
            now=BASE_TIME + timedelta(minutes=14),
        )

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.RESERVED
    )


def test_expire_reservation_is_idempotent():
    repository = make_repository(make_unit("UNIT-001"))

    reserve_inventory(
        repository,
        case_id=CASE_ID,
        request_id=REQUEST_ID,
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        expires_at=BASE_TIME + timedelta(minutes=15),
        reservation_id="RES-001",
    )

    first = expire_reservation(
        repository,
        "RES-001",
        now=BASE_TIME + timedelta(minutes=16),
    )

    second = expire_reservation(
        repository,
        "RES-001",
        now=BASE_TIME + timedelta(minutes=20),
    )

    assert first.status == InventoryReservationStatus.EXPIRED
    assert second.status == InventoryReservationStatus.EXPIRED


def test_expire_due_reservations_expires_only_due_reservations():
    repository = make_repository(
        make_unit("UNIT-001"),
        make_unit("UNIT-002"),
        make_unit("UNIT-003"),
    )

    reserve_inventory(
        repository,
        case_id="CASE-001",
        request_id="REQ-001",
        bank_id=BANK_ID,
        unit_ids=["UNIT-001"],
        expires_at=BASE_TIME - timedelta(minutes=1),
        reservation_id="RES-001",
    )

    reserve_inventory(
        repository,
        case_id="CASE-002",
        request_id="REQ-002",
        bank_id=BANK_ID,
        unit_ids=["UNIT-002"],
        expires_at=BASE_TIME + timedelta(minutes=10),
        reservation_id="RES-002",
    )

    reserve_inventory(
        repository,
        case_id="CASE-003",
        request_id="REQ-003",
        bank_id=BANK_ID,
        unit_ids=["UNIT-003"],
        reservation_id="RES-003",
    )

    expired = expire_due_reservations(
        repository,
        now=BASE_TIME,
    )

    assert len(expired) == 1
    assert expired[0].reservation_id == "RES-001"

    assert (
        repository.get_unit("UNIT-001").status
        == InventoryStatus.AVAILABLE
    )

    assert (
        repository.get_unit("UNIT-002").status
        == InventoryStatus.RESERVED
    )

    assert (
        repository.get_unit("UNIT-003").status
        == InventoryStatus.RESERVED
    )


# ---------------------------------------------------------------------------
# Missing resources
# ---------------------------------------------------------------------------


def test_reservation_rejects_unknown_unit():
    repository = make_repository()

    with pytest.raises(Exception):
        reserve_inventory(
            repository,
            case_id=CASE_ID,
            request_id=REQUEST_ID,
            bank_id=BANK_ID,
            unit_ids=["DOES-NOT-EXIST"],
        )


def test_consume_unknown_reservation_fails():
    repository = make_repository()

    with pytest.raises(Exception):
        consume_reservation(
            repository,
            "RES-DOES-NOT-EXIST",
        )


def test_release_unknown_reservation_fails():
    repository = make_repository()

    with pytest.raises(Exception):
        release_reservation(
            repository,
            "RES-DOES-NOT-EXIST",
        )


def test_transfer_receive_assigns_units_to_destination_and_is_idempotent():
    repository = make_repository(make_unit("UNIT-TRANSFER-001"))
    transfer = transfer_inventory(
        repository,
        transfer_id="TRANSFER-001",
        from_bank=BANK_ID,
        to_bank=OTHER_BANK_ID,
        unit_ids=["UNIT-TRANSFER-001"],
        reason="Network balancing",
    )

    received = receive_transfer(repository, transfer.transfer_id)
    repeated = receive_transfer(repository, transfer.transfer_id)

    assert received.status == "received"
    assert repeated.received_at == received.received_at
    assert repository.get_unit("UNIT-TRANSFER-001").bank_id == OTHER_BANK_ID
    assert repository.get_unit("UNIT-TRANSFER-001").status == InventoryStatus.AVAILABLE