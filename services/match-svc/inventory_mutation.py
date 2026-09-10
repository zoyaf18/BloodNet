"""
Inventory mutation domain layer for BloodNet.

Responsibilities:
    - Reserve specific inventory units for a case/request.
    - Consume a reservation when inventory is actually issued.
    - Release a reservation when it is no longer needed.
    - Expire stale reservations.
    - Prevent the same inventory unit from being actively reserved twice.

This module is intentionally storage-agnostic.

The current implementation uses an in-memory repository for local development
and testing. The repository interface can later be backed by Cloud SQL with
transactional row locking without changing the domain operations.

IMPORTANT:
    inventory_match.py is READ-ONLY.
    This module is the first layer allowed to mutate InventoryUnit.status.
"""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from threading import RLock
from uuid import uuid4

from contracts.models import (
    InventoryReservation,
    InventoryReservationStatus,
    InventoryStatus,
    InventoryUnit,
    Transfer,
)


class InventoryMutationError(Exception):
    """Base exception for inventory mutation failures."""


class InventoryUnitNotFoundError(InventoryMutationError):
    """Raised when a requested inventory unit does not exist."""


class InventoryReservationNotFoundError(InventoryMutationError):
    """Raised when a reservation does not exist."""


class InventoryUnitAlreadyReservedError(InventoryMutationError):
    """Raised when an inventory unit is already actively reserved."""


class InventoryUnitNotReservableError(InventoryMutationError):
    """Raised when a unit is not currently available for reservation."""


class InvalidReservationStateError(InventoryMutationError):
    """Raised when an operation is incompatible with reservation state."""


class TransferNotFoundError(InventoryMutationError):
    """Raised when a transfer does not exist."""


class InvalidTransferStateError(InventoryMutationError):
    """Raised when a transfer cannot be received."""


class InventoryRepository:
    """
    Storage abstraction for inventory mutations.

    The current implementation is an in-memory repository.

    A future Cloud SQL implementation should provide the same operations while
    using database transactions/row locks for concurrent reservation safety.
    """

    def __init__(
        self,
        units: list[InventoryUnit] | None = None,
        reservations: list[InventoryReservation] | None = None,
    ) -> None:
        self._units: dict[str, InventoryUnit] = {
            unit.unit_id: unit
            for unit in (units or [])
        }

        self._reservations: dict[str, InventoryReservation] = {
            reservation.reservation_id: reservation
            for reservation in (reservations or [])
        }
        self._transfers: dict[str, Transfer] = {}

        self._lock = RLock()

    # ------------------------------------------------------------------
    # Unit operations
    # ------------------------------------------------------------------

    def get_unit(self, unit_id: str) -> InventoryUnit:
        unit = self._units.get(unit_id)

        if unit is None:
            raise InventoryUnitNotFoundError(
                f"Inventory unit '{unit_id}' was not found."
            )

        return unit

    def save_unit(self, unit: InventoryUnit) -> None:
        self._units[unit.unit_id] = unit

    def get_units(self, unit_ids: list[str]) -> list[InventoryUnit]:
        return [self.get_unit(unit_id) for unit_id in unit_ids]

    def get_all_units(self) -> list[InventoryUnit]:
        return list(self._units.values())

    def get_units_for_update(self, unit_ids: list[str]) -> list[InventoryUnit]:
        return self.get_units(unit_ids)

    # ------------------------------------------------------------------
    # Reservation operations
    # ------------------------------------------------------------------

    def get_reservation(
        self,
        reservation_id: str,
    ) -> InventoryReservation:
        reservation = self._reservations.get(reservation_id)

        if reservation is None:
            raise InventoryReservationNotFoundError(
                f"Inventory reservation '{reservation_id}' was not found."
            )

        return reservation

    def save_reservation(
        self,
        reservation: InventoryReservation,
    ) -> None:
        self._reservations[reservation.reservation_id] = reservation

    def get_reservations(self) -> list[InventoryReservation]:
        return list(self._reservations.values())

    def get_active_reservation_for_unit(
        self,
        unit_id: str,
    ) -> InventoryReservation | None:
        active_states = {
            InventoryReservationStatus.RESERVED,
        }

        for reservation in self._reservations.values():
            if reservation.status not in active_states:
                continue

            if unit_id in reservation.unit_ids:
                return reservation

        return None

    def save_transfer(self, transfer: Transfer) -> None:
        self._transfers[transfer.transfer_id] = transfer

    def get_transfer(self, transfer_id: str) -> Transfer | None:
        return self._transfers.get(transfer_id)

    # ------------------------------------------------------------------
    # Transaction boundary
    # ------------------------------------------------------------------

    def transaction(self) -> Lock:
        """
        Return the repository transaction boundary.

        The in-memory implementation uses a process-level lock. Durable
        implementations provide a database transaction here.
        """
        return self._lock


class SQLiteInventoryRepository(InventoryRepository):
    """Durable local implementation of the inventory repository interface."""

    def __init__(
        self,
        db_path: str,
        units: list[InventoryUnit] | None = None,
        reservations: list[InventoryReservation] | None = None,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS inventory_units "
                "(unit_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS inventory_reservations "
                "(reservation_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            stored_units = connection.execute("SELECT payload FROM inventory_units").fetchall()
            stored_reservations = connection.execute("SELECT payload FROM inventory_reservations").fetchall()
        self._units = {
            unit.unit_id: unit
            for unit in (InventoryUnit.model_validate_json(row["payload"]) for row in stored_units)
        }
        self._reservations = {
            reservation.reservation_id: reservation
            for reservation in (InventoryReservation.model_validate_json(row["payload"]) for row in stored_reservations)
        }
        for unit in units or []:
            self.save_unit(unit)
        for reservation in reservations or []:
            self.save_reservation(reservation)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def get_unit(self, unit_id: str) -> InventoryUnit:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM inventory_units WHERE unit_id = ?", (unit_id,)
            ).fetchone()
        if row is None:
            raise InventoryUnitNotFoundError(f"Inventory unit '{unit_id}' was not found.")
        return InventoryUnit.model_validate_json(row["payload"])

    def save_unit(self, unit: InventoryUnit) -> None:
        self._units[unit.unit_id] = unit
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO inventory_units(unit_id, payload) VALUES (?, ?)",
                (unit.unit_id, unit.model_dump_json()),
            )

    def get_units(self, unit_ids: list[str]) -> list[InventoryUnit]:
        return [self.get_unit(unit_id) for unit_id in unit_ids]

    def get_reservation(self, reservation_id: str) -> InventoryReservation:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM inventory_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        if row is None:
            raise InventoryReservationNotFoundError(
                f"Inventory reservation '{reservation_id}' was not found."
            )
        return InventoryReservation.model_validate_json(row["payload"])

    def save_reservation(self, reservation: InventoryReservation) -> None:
        self._reservations[reservation.reservation_id] = reservation
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO inventory_reservations(reservation_id, payload) VALUES (?, ?)",
                (reservation.reservation_id, reservation.model_dump_json()),
            )

    def get_reservations(self) -> list[InventoryReservation]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM inventory_reservations ORDER BY rowid"
            ).fetchall()
        return [InventoryReservation.model_validate_json(row["payload"]) for row in rows]

    def get_active_reservation_for_unit(self, unit_id: str) -> InventoryReservation | None:
        return next(
            (reservation for reservation in self.get_reservations()
             if reservation.status == InventoryReservationStatus.RESERVED
             and unit_id in reservation.unit_ids),
            None,
        )


def _utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    # Legacy imported timestamps without an offset are stored as UTC.
    return expires_at.replace(tzinfo=timezone.utc) <= now if expires_at.tzinfo is None else expires_at <= now


def _require_unexpired(unit: InventoryUnit, now: datetime) -> None:
    if _is_expired(unit.expires_at, now):
        raise InventoryUnitNotReservableError(f"Inventory unit '{unit.unit_id}' has expired.")


def _new_reservation_id() -> str:
    """Generate a unique reservation ID."""
    return f"RES-{uuid4().hex}"


def _ensure_unique_unit_ids(unit_ids: list[str]) -> None:
    """Reject duplicate unit IDs in a reservation request."""
    if len(unit_ids) != len(set(unit_ids)):
        raise InventoryMutationError(
            "Reservation contains duplicate inventory unit IDs."
        )


def reserve_inventory(
    repository: InventoryRepository,
    *,
    case_id: str,
    request_id: str,
    bank_id: str,
    unit_ids: list[str],
    expires_at: datetime | None = None,
    reservation_id: str | None = None,
) -> InventoryReservation:
    """
    Reserve specific inventory units.

    Preconditions:
        - Every unit exists.
        - Every unit belongs to the supplied bank.
        - Every unit is AVAILABLE.
        - No unit is already actively reserved.

    Effects:
        - InventoryUnit.status becomes RESERVED.
        - A new InventoryReservation is created.

    This operation is atomic within the in-memory repository lock.

    The same reservation_id can safely be retried. If the reservation already
    exists with the same requested details, the existing reservation is
    returned instead of creating a duplicate.
    """

    if not unit_ids:
        raise InventoryMutationError(
            "Cannot create an inventory reservation with no units."
        )

    _ensure_unique_unit_ids(unit_ids)

    reservation_id = reservation_id or _new_reservation_id()
    now = _utc_now()

    with repository.transaction():
        # --------------------------------------------------------------
        # Idempotency: retrying the same reservation request
        # --------------------------------------------------------------
        existing = None

        try:
            existing = repository.get_reservation(reservation_id)
        except InventoryReservationNotFoundError:
            pass

        if existing is not None:
            if (
                existing.case_id != case_id
                or existing.request_id != request_id
                or existing.bank_id != bank_id
                or existing.unit_ids != unit_ids
            ):
                raise InventoryMutationError(
                    f"Reservation ID '{reservation_id}' already exists "
                    "with different reservation details."
                )

            return existing

        # --------------------------------------------------------------
        # Validate every requested unit before mutating anything.
        # --------------------------------------------------------------
        units = repository.get_units_for_update(unit_ids)

        for unit in units:
            _require_unexpired(unit, now)
            if unit.bank_id != bank_id:
                raise InventoryMutationError(
                    f"Inventory unit '{unit.unit_id}' belongs to bank "
                    f"'{unit.bank_id}', not '{bank_id}'."
                )

            if unit.status == InventoryStatus.RESERVED:
                raise InventoryUnitAlreadyReservedError(
                    f"Inventory unit '{unit.unit_id}' is already reserved."
                )

            if unit.status != InventoryStatus.AVAILABLE:
                raise InventoryUnitNotReservableError(
                    f"Inventory unit '{unit.unit_id}' is "
                    f"'{unit.status.value}', not available."
                )

            active_reservation = (
                repository.get_active_reservation_for_unit(unit.unit_id)
            )

            if active_reservation is not None:
                raise InventoryUnitAlreadyReservedError(
                    f"Inventory unit '{unit.unit_id}' is already reserved "
                    f"by reservation '{active_reservation.reservation_id}'."
                )

        # --------------------------------------------------------------
        # Only mutate after ALL validation has passed.
        # --------------------------------------------------------------
        for unit in units:
            unit.status = InventoryStatus.RESERVED
            repository.save_unit(unit)

        reservation = InventoryReservation(
            reservation_id=reservation_id,
            case_id=case_id,
            request_id=request_id,
            bank_id=bank_id,
            unit_ids=list(unit_ids),
            status=InventoryReservationStatus.RESERVED,
            created_at=now,
            expires_at=expires_at,
        )

        repository.save_reservation(reservation)

        return reservation


def transfer_inventory(
    repository: InventoryRepository,
    *,
    transfer_id: str,
    from_bank: str,
    to_bank: str,
    unit_ids: list[str],
    reason: str,
    case_id: str | None = None,
) -> Transfer:
    """Dispatch available units into a durable, idempotent transfer.

    Units remain owned by the source bank until a receiving workflow completes
    the transfer; ``in_transit`` prevents double allocation during transit.
    """
    if not from_bank or not to_bank or from_bank == to_bank:
        raise InventoryMutationError("A transfer requires distinct source and destination banks.")
    if not unit_ids:
        raise InventoryMutationError("A transfer requires at least one inventory unit.")
    _ensure_unique_unit_ids(unit_ids)

    with repository.transaction():
        existing = repository.get_transfer(transfer_id)
        if existing is not None:
            if existing.from_bank != from_bank or existing.to_bank != to_bank or existing.units != unit_ids:
                raise InventoryMutationError(f"Transfer ID '{transfer_id}' already exists with different details.")
            return existing

        units = repository.get_units_for_update(unit_ids)
        for unit in units:
            _require_unexpired(unit, _utc_now())
            if unit.bank_id != from_bank:
                raise InventoryMutationError(f"Inventory unit '{unit.unit_id}' is not owned by '{from_bank}'.")
            if unit.status != InventoryStatus.AVAILABLE:
                raise InventoryUnitNotReservableError(
                    f"Inventory unit '{unit.unit_id}' is '{unit.status.value}', not transferable."
                )
        for unit in units:
            unit.status = InventoryStatus.IN_TRANSIT
            repository.save_unit(unit)

        transfer = Transfer(
            transfer_id=transfer_id,
            from_bank=from_bank,
            to_bank=to_bank,
            units=list(unit_ids),
            reason=reason,
            case_id=case_id,
        )
        repository.save_transfer(transfer)
        database_url = getattr(repository, "_database_url", None)
        if database_url:
            import psycopg
            from psycopg.types.json import Jsonb
            psycopg_connection = getattr(repository, "_transaction_connection", None)
            connection = psycopg_connection.get() if psycopg_connection is not None else None
            if connection is None:
                raise InventoryMutationError("PostgreSQL transfer requires the repository transaction boundary.")
            connection.execute(
                """INSERT INTO inventory_transfers (transfer_id, payload, status)
                   VALUES (%s, %s, 'in_transit') ON CONFLICT (transfer_id) DO NOTHING""",
                (transfer.transfer_id, Jsonb(transfer.model_dump(mode="json"))),
            )
        return transfer


def receive_transfer(repository: InventoryRepository, transfer_id: str) -> Transfer:
    """Complete an in-transit transfer and assign units to the destination bank."""
    with repository.transaction():
        transfer = repository.get_transfer(transfer_id)
        if transfer is None:
            raise TransferNotFoundError(f"Transfer '{transfer_id}' was not found.")
        if transfer.status == "received":
            return transfer
        if transfer.status != "in_transit":
            raise InvalidTransferStateError(
                f"Transfer '{transfer_id}' cannot be received from state '{transfer.status}'."
            )
        units = repository.get_units_for_update(transfer.units)
        for unit in units:
            _require_unexpired(unit, _utc_now())
            if unit.bank_id != transfer.from_bank or unit.status != InventoryStatus.IN_TRANSIT:
                raise InventoryMutationError(
                    f"Inventory unit '{unit.unit_id}' is not in transit from '{transfer.from_bank}'."
                )
        for unit in units:
            unit.bank_id = transfer.to_bank
            unit.status = InventoryStatus.AVAILABLE
            repository.save_unit(unit)
        transfer.status = "received"
        transfer.received_at = datetime.now(timezone.utc)
        repository.save_transfer(transfer)
        database_url = getattr(repository, "_database_url", None)
        if database_url:
            connection_holder = getattr(repository, "_transaction_connection", None)
            connection = connection_holder.get() if connection_holder is not None else None
            if connection is None:
                raise InventoryMutationError("PostgreSQL transfer requires the repository transaction boundary.")
            from psycopg.types.json import Jsonb
            connection.execute(
                "UPDATE inventory_transfers SET payload = %s, status = 'received', updated_at = NOW() WHERE transfer_id = %s",
                (Jsonb(transfer.model_dump(mode="json")), transfer_id),
            )
        return transfer


def consume_reservation(
    repository: InventoryRepository,
    reservation_id: str,
) -> InventoryReservation:
    """
    Consume a reservation and issue its inventory units.

    State transition:

        RESERVED reservation
            -> CONSUMED reservation

        RESERVED inventory units
            -> ISSUED inventory units

    Idempotency:
        Calling consume_reservation() again on an already CONSUMED
        reservation returns the existing reservation.
    """

    with repository.transaction():
        reservation = repository.get_reservation(reservation_id)

        if reservation.status == InventoryReservationStatus.CONSUMED:
            return reservation

        if reservation.status != InventoryReservationStatus.RESERVED:
            raise InvalidReservationStateError(
                f"Reservation '{reservation_id}' cannot be consumed from "
                f"state '{reservation.status.value}'."
            )

        now = _utc_now()
        if reservation.expires_at is not None and _is_expired(reservation.expires_at, now):
            raise InvalidReservationStateError(f"Reservation '{reservation_id}' has expired.")

        units = repository.get_units_for_update(reservation.unit_ids)

        for unit in units:
            _require_unexpired(unit, now)
            if unit.status != InventoryStatus.RESERVED:
                raise InvalidReservationStateError(
                    f"Inventory unit '{unit.unit_id}' is "
                    f"'{unit.status.value}', expected 'reserved'."
                )

        for unit in units:
            unit.status = InventoryStatus.ISSUED
            repository.save_unit(unit)

        reservation.status = InventoryReservationStatus.CONSUMED
        reservation.consumed_at = _utc_now()

        repository.save_reservation(reservation)

        return reservation


def release_reservation(
    repository: InventoryRepository,
    reservation_id: str,
) -> InventoryReservation:
    """
    Release a reservation and return its units to AVAILABLE.

    State transition:

        RESERVED reservation
            -> RELEASED reservation

        RESERVED inventory units
            -> AVAILABLE inventory units

    Idempotency:
        Calling release_reservation() again on an already RELEASED
        reservation returns the existing reservation.

    A consumed reservation cannot be released.
    """

    with repository.transaction():
        reservation = repository.get_reservation(reservation_id)

        if reservation.status == InventoryReservationStatus.RELEASED:
            return reservation

        if reservation.status != InventoryReservationStatus.RESERVED:
            raise InvalidReservationStateError(
                f"Reservation '{reservation_id}' cannot be released from "
                f"state '{reservation.status.value}'."
            )

        units = repository.get_units_for_update(reservation.unit_ids)

        for unit in units:
            if unit.status != InventoryStatus.RESERVED:
                raise InvalidReservationStateError(
                    f"Inventory unit '{unit.unit_id}' is "
                    f"'{unit.status.value}', expected 'reserved'."
                )

        for unit in units:
            unit.status = InventoryStatus.DISCARDED if _is_expired(unit.expires_at, _utc_now()) else InventoryStatus.AVAILABLE
            repository.save_unit(unit)

        reservation.status = InventoryReservationStatus.RELEASED
        reservation.released_at = _utc_now()

        repository.save_reservation(reservation)

        return reservation


def expire_reservation(
    repository: InventoryRepository,
    reservation_id: str,
    *,
    now: datetime | None = None,
) -> InventoryReservation:
    """
    Expire a reservation whose expiry time has passed.

    State transition:

        RESERVED reservation
            -> EXPIRED reservation

        RESERVED inventory units
            -> AVAILABLE inventory units

    This is separate from release so expiry has an explicit audit state.
    """

    current_time = now or _utc_now()

    with repository.transaction():
        reservation = repository.get_reservation(reservation_id)

        if reservation.status == InventoryReservationStatus.EXPIRED:
            return reservation

        if reservation.status != InventoryReservationStatus.RESERVED:
            raise InvalidReservationStateError(
                f"Reservation '{reservation_id}' cannot expire from "
                f"state '{reservation.status.value}'."
            )

        if reservation.expires_at is None:
            raise InvalidReservationStateError(
                f"Reservation '{reservation_id}' has no expiry timestamp."
            )

        if current_time < reservation.expires_at:
            raise InvalidReservationStateError(
                f"Reservation '{reservation_id}' has not expired yet."
            )

        units = repository.get_units_for_update(reservation.unit_ids)

        for unit in units:
            if unit.status != InventoryStatus.RESERVED:
                raise InvalidReservationStateError(
                    f"Inventory unit '{unit.unit_id}' is "
                    f"'{unit.status.value}', expected 'reserved'."
                )

        for unit in units:
            unit.status = InventoryStatus.DISCARDED if _is_expired(unit.expires_at, current_time) else InventoryStatus.AVAILABLE
            repository.save_unit(unit)

        reservation.status = InventoryReservationStatus.EXPIRED

        repository.save_reservation(reservation)

        return reservation


def expire_due_reservations(
    repository: InventoryRepository,
    *,
    now: datetime | None = None,
) -> list[InventoryReservation]:
    """
    Expire every currently RESERVED reservation whose expiry time has passed.

    Reservations without an expiry timestamp are ignored.
    """

    current_time = now or _utc_now()

    expired: list[InventoryReservation] = []

    # Snapshot first because expiration mutates reservation state.
    reservations = repository.get_reservations()

    for reservation in reservations:
        if reservation.status != InventoryReservationStatus.RESERVED:
            continue

        if reservation.expires_at is None:
            continue

        if current_time < reservation.expires_at:
            continue

        expired.append(
            expire_reservation(
                repository,
                reservation.reservation_id,
                now=current_time,
            )
        )

    return expired
