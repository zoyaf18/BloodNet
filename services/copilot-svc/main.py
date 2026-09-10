from datetime import datetime
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

import psycopg
from psycopg.rows import dict_row
from contracts.models import BloodBank, Component, InventoryUnit
from contracts.inventory_payload import _inventory_unit_from_payload
from transfer_optimizer import recommend_transfer
from contracts.auth import Identity, get_identity, require_role

app = FastAPI(
    title="BloodNet Inventory Copilot",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class TransferRequest(BaseModel):
    destination_bank_id: str
    group: str
    component: Component
    quantity: int = Field(ge=1, le=1000)
    lead_time_hours: float = Field(default=4.0, ge=0)
    cold_chain_buffer_hours: float = Field(default=2.0, ge=0)
    now: datetime | None = None
    route_constraints: dict[str, float] | None = None


def _live_transfer_inputs(identity: Identity) -> tuple[list[InventoryUnit], list[BloodBank]]:
    database_url = os.getenv("BLOODNET_DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="Inventory database is unavailable")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        if identity.role == "bank_admin" and not identity.bank_id:
            raise HTTPException(status_code=403, detail="Bank identity is missing resource scope")
        rows = connection.execute(
            "SELECT payload FROM inventory_units WHERE payload->>'bank_id' = %s" if identity.bank_id else "SELECT payload FROM inventory_units",
            (identity.bank_id,) if identity.bank_id else (),
        ).fetchall()
        units = []
        for row in rows:
            try:
                units.append(_inventory_unit_from_payload(row["payload"]))
            except (ValueError, TypeError):
                continue
        organizations = connection.execute(
            "SELECT id::text, name, type, metadata FROM organizations WHERE status = 'active' AND type = 'blood_bank'"
        ).fetchall()
    banks = []
    for organization in organizations:
        metadata = organization["metadata"] or {}
        bank_id = str(metadata.get("bank_id") or organization["id"])
        geo = metadata.get("geo")
        if isinstance(geo, dict) and {"lat", "lng"}.issubset(geo):
            banks.append(BloodBank(
                bank_id=bank_id,
                name=organization["name"],
                geo=geo,
                licence_id=str(metadata.get("licence_id") or bank_id),
                min_reserve=metadata.get("min_reserve", {}),
            ))
    if identity.bank_id:
        units = [unit for unit in units if unit.bank_id == identity.bank_id]
    return units, banks


@app.post("/api/v1/copilot/transfer")
def transfer(payload: TransferRequest, identity: Identity = Depends(get_identity)) -> dict[str, Any]:
    require_role(identity, "bank_admin", "regional_admin", "auditor")
    try:
        units, banks = _live_transfer_inputs(identity)
        if identity.bank_id and payload.destination_bank_id == identity.bank_id:
            raise ValueError("Destination bank must differ from the source bank")
        recommendation = recommend_transfer(
            units=units,
            banks=banks,
            **payload.model_dump(),
        )
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Inventory data store is temporarily unavailable") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if recommendation is None:
        return {"status": "no_recommendation", "data": {"reason": "No safe surplus satisfies reserve and lead-time constraints."}}
    return {"status": "success", "data": recommendation.model_dump(mode="json")}
