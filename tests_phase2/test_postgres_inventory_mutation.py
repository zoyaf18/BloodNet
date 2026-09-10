from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import threading

import psycopg
import pytest

from contracts.models import (
    BloodGroup,
    Component,
    InventoryReservationStatus,
    InventoryStatus,
    InventoryUnit,
)

MATCH_SVC_DIR = Path(__file__).resolve().parents[1] / "services" / "match-svc"
if str(MATCH_SVC_DIR) not in sys.path:
    sys.path.insert(0, str(MATCH_SVC_DIR))

from inventory_mutation import (
    InventoryMutationError,
    InventoryUnitAlreadyReservedError,
    InventoryUnitNotReservableError,
    consume_reservation,
    expire_reservation,
    release_reservation,
    reserve_inventory,
)
from postgres_inventory_repository import PostgreSQLInventoryRepository


DATABASE_URL = os.environ.get("BLOODNET_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="BLOODNET_DATABASE_URL is required for PostgreSQL tests",
)
BASE_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
TEST_PREFIX = "PG-TEST-"


def make_unit(unit_id: str, status: InventoryStatus = InventoryStatus.AVAILABLE):
    return InventoryUnit(
        unit_id=unit_id,
        bank_id="BANK-001",
        group=BloodGroup.A_POS,
        component=Component.RBC,
        collected_at=BASE_TIME - timedelta(days=10),
        expires_at=BASE_TIME + timedelta(days=32),
        status=status,
    )


@pytest.fixture
def repository():
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM inventory_reservations WHERE reservation_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM inventory_units WHERE unit_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
    repo = PostgreSQLInventoryRepository(DATABASE_URL)
    yield repo
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            "DELETE FROM inventory_reservations WHERE reservation_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
        connection.execute(
            "DELETE FROM inventory_units WHERE unit_id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )


def save_units(repository, *units):
    for unit in units:
        repository.save_unit(unit)


def test_same_unit_cannot_be_reserved_twice(repository):
    unit = make_unit(f"{TEST_PREFIX}DOUBLE-UNIT")
    save_units(repository, unit)
    reserve_inventory(
        repository,
        case_id=f"{TEST_PREFIX}CASE-1",
        request_id=f"{TEST_PREFIX}REQ-1",
        bank_id=unit.bank_id,
        unit_ids=[unit.unit_id],
        reservation_id=f"{TEST_PREFIX}RES-1",
    )

    with pytest.raises(InventoryUnitAlreadyReservedError):
        reserve_inventory(
            repository,
            case_id=f"{TEST_PREFIX}CASE-2",
            request_id=f"{TEST_PREFIX}REQ-2",
            bank_id=unit.bank_id,
            unit_ids=[unit.unit_id],
            reservation_id=f"{TEST_PREFIX}RES-2",
        )


def test_reservation_validation_is_atomic(repository):
    valid = make_unit(f"{TEST_PREFIX}ATOMIC-VALID")
    invalid = make_unit(f"{TEST_PREFIX}ATOMIC-INVALID", InventoryStatus.ISSUED)
    save_units(repository, valid, invalid)

    with pytest.raises(InventoryUnitNotReservableError):
        reserve_inventory(
            repository,
            case_id=f"{TEST_PREFIX}CASE-ATOMIC",
            request_id=f"{TEST_PREFIX}REQ-ATOMIC",
            bank_id=valid.bank_id,
            unit_ids=[valid.unit_id, invalid.unit_id],
            reservation_id=f"{TEST_PREFIX}RES-ATOMIC",
        )

    assert repository.get_unit(valid.unit_id).status is InventoryStatus.AVAILABLE
    assert repository.get_unit(invalid.unit_id).status is InventoryStatus.ISSUED


def test_reservation_idempotency(repository):
    unit = make_unit(f"{TEST_PREFIX}IDEMPOTENT-UNIT")
    save_units(repository, unit)
    first = reserve_inventory(
        repository,
        case_id=f"{TEST_PREFIX}CASE-IDEMPOTENT",
        request_id=f"{TEST_PREFIX}REQ-IDEMPOTENT",
        bank_id=unit.bank_id,
        unit_ids=[unit.unit_id],
        reservation_id=f"{TEST_PREFIX}RES-IDEMPOTENT",
    )
    second = reserve_inventory(
        repository,
        case_id=first.case_id,
        request_id=first.request_id,
        bank_id=first.bank_id,
        unit_ids=first.unit_ids,
        reservation_id=first.reservation_id,
    )

    assert second == first
    test_reservations = [
        item
        for item in repository.get_reservations()
        if item.reservation_id.startswith(TEST_PREFIX)
    ]
    assert len(test_reservations) == 1


def test_consume_release_and_expire(repository):
    consume_unit = make_unit(f"{TEST_PREFIX}CONSUME-UNIT")
    release_unit = make_unit(f"{TEST_PREFIX}RELEASE-UNIT")
    expire_unit = make_unit(f"{TEST_PREFIX}EXPIRE-UNIT")
    save_units(repository, consume_unit, release_unit, expire_unit)

    consumed = reserve_inventory(
        repository,
        case_id=f"{TEST_PREFIX}CASE-CONSUME",
        request_id=f"{TEST_PREFIX}REQ-CONSUME",
        bank_id="BANK-001",
        unit_ids=[consume_unit.unit_id],
        reservation_id=f"{TEST_PREFIX}RES-CONSUME",
    )
    assert consume_reservation(repository, consumed.reservation_id).status is InventoryReservationStatus.CONSUMED
    assert repository.get_unit(consume_unit.unit_id).status is InventoryStatus.ISSUED

    released = reserve_inventory(
        repository,
        case_id=f"{TEST_PREFIX}CASE-RELEASE",
        request_id=f"{TEST_PREFIX}REQ-RELEASE",
        bank_id="BANK-001",
        unit_ids=[release_unit.unit_id],
        reservation_id=f"{TEST_PREFIX}RES-RELEASE",
    )
    assert release_reservation(repository, released.reservation_id).status is InventoryReservationStatus.RELEASED
    assert repository.get_unit(release_unit.unit_id).status is InventoryStatus.AVAILABLE

    expired = reserve_inventory(
        repository,
        case_id=f"{TEST_PREFIX}CASE-EXPIRE",
        request_id=f"{TEST_PREFIX}REQ-EXPIRE",
        bank_id="BANK-001",
        unit_ids=[expire_unit.unit_id],
        expires_at=BASE_TIME - timedelta(minutes=1),
        reservation_id=f"{TEST_PREFIX}RES-EXPIRE",
    )
    assert expire_reservation(repository, expired.reservation_id, now=BASE_TIME).status is InventoryReservationStatus.EXPIRED
    assert repository.get_unit(expire_unit.unit_id).status is InventoryStatus.AVAILABLE


def test_two_connections_only_one_reservation_succeeds(repository):
    unit = make_unit(f"{TEST_PREFIX}RACE-UNIT")
    repository.save_unit(unit)
    first = PostgreSQLInventoryRepository(DATABASE_URL)
    second = PostgreSQLInventoryRepository(DATABASE_URL)
    results = []
    barrier = threading.Barrier(2)

    def attempt(repo, reservation_id):
        barrier.wait()
        try:
            reserve_inventory(
                repo,
                case_id=f"{TEST_PREFIX}{reservation_id}-CASE",
                request_id=f"{TEST_PREFIX}{reservation_id}-REQ",
                bank_id=unit.bank_id,
                unit_ids=[unit.unit_id],
                reservation_id=f"{TEST_PREFIX}{reservation_id}",
            )
        except InventoryUnitAlreadyReservedError:
            results.append("rejected")
        else:
            results.append("succeeded")

    threads = [
        threading.Thread(target=attempt, args=(first, "RACE-A")),
        threading.Thread(target=attempt, args=(second, "RACE-B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == ["rejected", "succeeded"]
    assert repository.get_unit(unit.unit_id).status is InventoryStatus.RESERVED
