from datetime import datetime, timedelta, timezone

from contracts.models import InventoryUnit

import sys
from pathlib import Path

MATCH_DIR = Path(__file__).resolve().parents[1] / "services" / "match-svc"
sys.path.insert(0, str(MATCH_DIR))
from inventory_mutation import SQLiteInventoryRepository, reserve_inventory
sys.path.remove(str(MATCH_DIR))


def test_sqlite_repository_survives_reopen(tmp_path):
	now = datetime.now(timezone.utc)
	unit = InventoryUnit(
		unit_id="UNIT-1", bank_id="BANK-1", group="O+", component="RBC",
		collected_at=now, expires_at=now + timedelta(days=30),
	)
	database = str(tmp_path / "inventory.db")
	first = SQLiteInventoryRepository(database, units=[unit])
	reservation = reserve_inventory(
		first, case_id="CASE-1", request_id="REQ-1", bank_id="BANK-1",
		unit_ids=[unit.unit_id], reservation_id="RES-1",
	)

	reopened = SQLiteInventoryRepository(database)

	assert reopened.get_unit(unit.unit_id).status.value == "reserved"
	assert reopened.get_reservation(reservation.reservation_id).case_id == "CASE-1"
