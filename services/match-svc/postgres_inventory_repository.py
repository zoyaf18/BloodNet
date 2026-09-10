from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from contracts.models import (
    InventoryReservation,
    InventoryReservationStatus,
    InventoryUnit,
    Transfer,
)
from inventory_mutation import (
    InventoryRepository,
    InventoryReservationNotFoundError,
    InventoryUnitNotFoundError,
)


from contracts.inventory_payload import _inventory_unit_from_payload


class PostgreSQLInventoryRepository(InventoryRepository):
    """PostgreSQL-backed inventory repository.

    Schema management belongs to the migration runner. The repository only
    opens connections and persists inventory models as JSONB payloads.
    """

    def __init__(
        self,
        database_url: str | None = None,
    ) -> None:
        super().__init__()
        self._database_url = database_url or os.environ["BLOODNET_DATABASE_URL"]
        self._transaction_connection: ContextVar[psycopg.Connection | None] = ContextVar(
            "postgres_inventory_transaction_connection",
            default=None,
        )

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        connection = self._transaction_connection.get()
        if connection is not None:
            yield connection
            return

        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            yield connection

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Provide the transaction boundary used by inventory mutations."""
        if self._transaction_connection.get() is not None:
            yield self._transaction_connection.get()
            return

        with psycopg.connect(self._database_url, row_factory=dict_row) as connection:
            token = self._transaction_connection.set(connection)
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self._transaction_connection.reset(token)

    def get_unit(self, unit_id: str) -> InventoryUnit:
        with self._connect() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT payload
                    FROM inventory_units
                    WHERE unit_id = %s
                    """,
                    (unit_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise InventoryUnitNotFoundError(f"Inventory unit '{unit_id}' was not found.")
        return _inventory_unit_from_payload(row["payload"])

    def save_unit(self, unit: InventoryUnit) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO inventory_units (
                        unit_id,
                        payload,
                        updated_at
                    )
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (unit_id)
                    DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    (
                        unit.unit_id,
                        Jsonb(unit.model_dump(mode="json")),
                    ),
                )

    def get_units(self, unit_ids: list[str]) -> list[InventoryUnit]:
        if not unit_ids:
            return []

        placeholders = ", ".join("%s" for _ in unit_ids)
        lock_clause = " FOR UPDATE" if self._transaction_connection.get() else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT unit_id, payload FROM inventory_units "
                f"WHERE unit_id IN ({placeholders})" + lock_clause,
                tuple(unit_ids),
            ).fetchall()

        units_by_id = {
            row["unit_id"]: _inventory_unit_from_payload(row["payload"])
            for row in rows
        }
        return [
            units_by_id[unit_id]
            if unit_id in units_by_id
            else self.get_unit(unit_id)
            for unit_id in unit_ids
        ]

    def get_all_units(self) -> list[InventoryUnit]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT unit_id, payload FROM inventory_units"
            ).fetchall()
        # A historical or externally synchronized malformed record must not
        # make every case list and dashboard request fail. Keep the bad row
        # out of operational matching, log it for repair, and return the valid
        # inventory that remains.
        units: list[InventoryUnit] = []
        for row in rows:
            try:
                units.append(_inventory_unit_from_payload(row["payload"]))
            except (ValidationError, ValueError, TypeError) as exc:
                logging.getLogger(__name__).warning("Ignoring invalid inventory payload for unit %s: %s", row["unit_id"], exc)
        return units

    def get_units_for_update(self, unit_ids: list[str]) -> list[InventoryUnit]:
        if not unit_ids:
            return []

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT unit_id, payload
                FROM inventory_units
                WHERE unit_id = ANY(%s)
                ORDER BY unit_id
                FOR UPDATE
                """,
                (unit_ids,),
            ).fetchall()

        units_by_id = {
            row["unit_id"]: _inventory_unit_from_payload(row["payload"])
            for row in rows
        }
        return [
            units_by_id[unit_id]
            if unit_id in units_by_id
            else self.get_unit(unit_id)
            for unit_id in unit_ids
        ]

    def get_reservation(self, reservation_id: str) -> InventoryReservation:
        with self._connect() as connection:
            lock_clause = " FOR UPDATE" if self._transaction_connection.get() else ""
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT payload
                    FROM inventory_reservations
                    WHERE reservation_id = %s
                    """ + lock_clause,
                    (reservation_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise InventoryReservationNotFoundError(
                f"Inventory reservation '{reservation_id}' was not found."
            )
        return InventoryReservation.model_validate(row["payload"])

    def save_reservation(self, reservation: InventoryReservation) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO inventory_reservations (
                        reservation_id,
                        payload,
                        updated_at
                    )
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (reservation_id)
                    DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = NOW()
                    """,
                    (
                        reservation.reservation_id,
                        Jsonb(reservation.model_dump(mode="json")),
                    ),
                )

    def get_reservations(self) -> list[InventoryReservation]:
        with self._connect() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(
                    """
                    SELECT payload
                    FROM inventory_reservations
                    ORDER BY created_at, reservation_id
                    """
                )
                rows = cursor.fetchall()
        return [InventoryReservation.model_validate(row["payload"]) for row in rows]

    def get_active_reservation_for_unit(
        self,
        unit_id: str,
    ) -> InventoryReservation | None:
        reservations = self.get_reservations()

        return next(
            (
                reservation
                for reservation in reservations
                if (
                    reservation.status == InventoryReservationStatus.RESERVED
                    and unit_id in reservation.unit_ids
                )
            ),
            None,
        )

    def get_transfer(self, transfer_id: str) -> Transfer | None:
        """Load transfers from Cloud SQL so receipt survives process restarts."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM inventory_transfers WHERE transfer_id = %s" +
                (" FOR UPDATE" if self._transaction_connection.get() is not None else ""),
                (transfer_id,),
            ).fetchone()
        return Transfer.model_validate(row["payload"]) if row else None

    def save_transfer(self, transfer: Transfer) -> None:
        self._transfers[transfer.transfer_id] = transfer
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO inventory_transfers (transfer_id, payload, status, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (transfer_id) DO UPDATE SET
                    payload = EXCLUDED.payload,
                    status = EXCLUDED.status,
                    updated_at = NOW()
                """,
                (transfer.transfer_id, Jsonb(transfer.model_dump(mode="json")), transfer.status),
            )

    def record_approval_decision(
        self,
        cursor: psycopg.Cursor,
        approval_id: str,
        rec_id: str,
        case_id: str,
        decision: str,
        audit_id: str,
        outbox_event_id: str | None = None,
    ) -> None:
        """
        Record an approval decision atomically within an existing transaction.
        
        This ensures that the approval state, audit record, and outbox event
        (if any) are all committed together or all rolled back together.
        
        Args:
            cursor: Active database cursor within a transaction
            approval_id: Unique identifier for this approval decision
            rec_id: Recommendation ID being approved/rejected
            case_id: Case ID for audit trail
            decision: "APPROVED" or "REJECTED"
            audit_id: ID of the audit record for this decision
            outbox_event_id: Optional event ID to track with this approval
        """
        cursor.execute(
            """
            INSERT INTO approval_state_tracking (
                approval_id,
                rec_id,
                case_id,
                decision,
                audit_id,
                outbox_event_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (approval_id)
            DO NOTHING
            """,
            (
                approval_id,
                rec_id,
                case_id,
                decision,
                audit_id,
                outbox_event_id,
            ),
        )

    def claim_approval_decision(
        self,
        cursor: psycopg.Cursor,
        approval_id: str,
        rec_id: str,
        case_id: str,
        decision: str,
        audit_id: str,
        outbox_event_id: str | None = None,
    ) -> bool:
        """Atomically claim an approval ID for the current transaction."""
        cursor.execute(
            """
            INSERT INTO approval_state_tracking (
                approval_id, rec_id, case_id, decision, audit_id, outbox_event_id
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (approval_id) DO NOTHING
            """,
            (approval_id, rec_id, case_id, decision, audit_id, outbox_event_id),
        )
        return cursor.rowcount == 1
