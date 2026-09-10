"""Recommendation-only inventory transfer optimization for local/demo use."""

from __future__ import annotations

from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
from typing import Iterable
from uuid import uuid4

from contracts.models import BloodBank, Component, InventoryUnit, Recommendation

EARTH_RADIUS_KM = 6371.0


def _haversine_km(a, b) -> float:
    lat1, lng1, lat2, lng2 = map(radians, (a.lat, a.lng, b.lat, b.lng))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * atan2(sqrt(h), sqrt(1 - h))


def _expiry_risk(unit: InventoryUnit, *, now: datetime, lead_time_hours: float, cold_chain_buffer_hours: float) -> float:
    remaining_hours = max((unit.expires_at - now).total_seconds() / 3600.0, 0.0)
    safety_window = lead_time_hours + cold_chain_buffer_hours
    if remaining_hours <= 0:
        return 1.0
    if remaining_hours <= safety_window:
        return 1.0 - (remaining_hours / max(safety_window, 1e-6))
    return 0.0


def _select_units_with_ortools(
    units: list[InventoryUnit],
    quantity: int,
    *,
    now: datetime,
    lead_time_hours: float,
    cold_chain_buffer_hours: float,
) -> list[InventoryUnit] | None:
    """Select an exact quantity with a bounded binary optimization model."""
    try:
        from ortools.linear_solver import pywraplp
    except ImportError:
        return None
    solver = pywraplp.Solver.CreateSolver("SCIP") or pywraplp.Solver.CreateSolver("CBC")
    if solver is None:
        return None
    variables = [solver.BoolVar(f"unit_{index}") for index in range(len(units))]
    solver.Add(sum(variables) == quantity)
    solver.Minimize(sum(
        variables[index] * (
            _expiry_risk(
                unit,
                now=now,
                lead_time_hours=lead_time_hours,
                cold_chain_buffer_hours=cold_chain_buffer_hours,
            )
            + index / max(len(units), 1_000_000)
        )
        for index, unit in enumerate(units)
    ))
    if solver.Solve() != pywraplp.Solver.OPTIMAL:
        return None
    return [unit for unit, variable in zip(units, variables) if variable.solution_value() > 0.5]


def recommend_transfer(
    *,
    units: Iterable[InventoryUnit],
    banks: Iterable[BloodBank],
    destination_bank_id: str,
    group: str,
    component: Component,
    quantity: int,
    lead_time_hours: float = 4.0,
    cold_chain_buffer_hours: float = 2.0,
    now: datetime | None = None,
    recommendation_id: str | None = None,
    route_constraints: dict | None = None,
) -> Recommendation | None:
    """Choose the safest surplus units that respect reserve, expiry, and route constraints."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    now = now or datetime.now(timezone.utc)
    units = list(units)
    bank_map = {bank.bank_id: bank for bank in banks}
    destination = bank_map.get(destination_bank_id)
    if destination is None:
        raise ValueError(f"Unknown destination bank '{destination_bank_id}'")
    group_name = group.value if hasattr(group, "value") else str(group)
    component_name = component.value if hasattr(component, "value") else str(component)
    route_constraints = route_constraints or {}
    max_transfer_hours = float(route_constraints.get("max_transfer_hours", lead_time_hours + cold_chain_buffer_hours))
    max_distance_km = float(route_constraints.get("max_distance_km", float("inf")))

    candidates = [
        unit for unit in units
        if unit.status.value == "available"
        and unit.bank_id != destination_bank_id
        and unit.group.value == group_name
        and unit.component == component
        and (unit.expires_at - now).total_seconds() > (lead_time_hours + cold_chain_buffer_hours) * 3600
    ]
    by_bank: dict[str, list[InventoryUnit]] = {}
    for unit in candidates:
        by_bank.setdefault(unit.bank_id, []).append(unit)

    best_bank = None
    best_units: list[InventoryUnit] = []
    best_score = None
    for bank_id, bank_units in sorted(by_bank.items()):
        source = bank_map.get(bank_id)
        if source is None:
            continue

        reserve_key = f"{group_name}|{component_name}"
        reserve = int(source.min_reserve.get(reserve_key, 0))
        available_count = sum(
            1 for unit in units
            if unit.bank_id == bank_id and unit.status.value == "available"
            and unit.group.value == group_name and unit.component == component
        )
        transferable = max(available_count - reserve, 0)
        if transferable <= 0:
            continue

        distance_km = _haversine_km(source.geo, destination.geo)
        if distance_km > max_distance_km:
            continue
        if lead_time_hours > max_transfer_hours:
            continue

        eligible = sorted(bank_units, key=lambda unit: (unit.expires_at, unit.unit_id))
        if len(eligible) < min(quantity, transferable):
            continue

        target_quantity = min(quantity, transferable)
        selected = _select_units_with_ortools(
            eligible,
            target_quantity,
            now=now,
            lead_time_hours=lead_time_hours,
            cold_chain_buffer_hours=cold_chain_buffer_hours,
        ) or eligible[:target_quantity]
        score = (
            sum(
                _expiry_risk(unit, now=now, lead_time_hours=lead_time_hours, cold_chain_buffer_hours=cold_chain_buffer_hours)
                for unit in selected
            ) / max(len(selected), 1),
            distance_km,
            source.bank_id,
        )
        if best_score is None or score < best_score:
            best_score = score
            best_bank = source
            best_units = selected

    if best_bank is None or len(best_units) < quantity:
        return None

    unit_ids = [unit.unit_id for unit in best_units]
    return Recommendation(
        rec_id=recommendation_id or f"REC-TRANSFER-{uuid4().hex}",
        type="TRANSFER_INVENTORY",
        payload={
            "from_bank": best_bank.bank_id,
            "to_bank": destination_bank_id,
            "group": group_name,
            "component": component_name,
            "unit_ids": unit_ids,
            "units": quantity,
            "route_constraints": route_constraints,
        },
        rationale=(f"Move {quantity} safe-to-transport {group_name} {component_name} units from {best_bank.bank_id} while retaining its minimum reserve and honoring route/cold-chain constraints."),
        expected_impact={"shortage_units_delta": -quantity, "expiry_risk_delta": -round(min(1.0, best_score[0]), 6) if best_score is not None else 0.0},
        provenance={"optimizer": "ortools_binary_selection", "generated_at": now.isoformat(), "route_constraints": route_constraints},
        state="AWAITING_APPROVAL",
    )
