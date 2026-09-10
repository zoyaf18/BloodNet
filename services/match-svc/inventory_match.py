"""
Inventory branch of match-svc (SPEC v1.1, §6.1 step 4b).

Runs concurrently with the donor-ranking branch, not after it: given an
incoming Request, find compatible on-hand units at nearby blood banks so the
swarm only ever mobilizes donors for the shortfall, not the whole request.

Distance/ETA here uses haversine + a flat average-speed assumption as a
stand-in for the Maps Routes API call the real match-svc will make (SPEC
§5.2) — swap `eta_minutes` for a real Routes API client without touching the
matching/allocation logic below.

IMPORTANT:
    This module is READ-ONLY.

    `selected_unit_ids` represents the proposed inventory allocation. It does
    NOT reserve those units. Actual reservation/consumption/release happens
    later through the inventory mutation layer after approval.
"""

from __future__ import annotations

from datetime import timedelta
from contracts.travel_time import build_travel_time_provider, haversine_km

from pydantic import BaseModel

from compatibility import acceptable_donor_groups
from contracts.models import (
    BloodBank,
    GeoPoint,
    Hospital,
    InventoryMatch,
    InventoryStatus,
    InventoryUnit,
    Request,
)


COLD_CHAIN_BUFFER_DAYS = 1
# Unit must still have this much shelf life remaining after arrival.


_TRAVEL_TIME_PROVIDER = build_travel_time_provider()


def eta_minutes(origin: GeoPoint, destination: GeoPoint) -> float:
    return _TRAVEL_TIME_PROVIDER.minutes(origin, destination)


class InventoryMatchResult(BaseModel):
    """
    Read-only inventory matching result.

    `matches` contains candidate blood banks and the exact unit IDs selected
    for the proposed fulfillment plan.

    `units_from_inventory` is the number of request units that can be
    fulfilled from currently available inventory.

    `units_remaining` is the remaining shortfall that donor/swarm
    mobilization must address.

    No inventory reservation or mutation occurs in this module.
    """

    matches: list[InventoryMatch]
    units_from_inventory: int
    units_remaining: int


def find_inventory_matches(
    request: Request,
    hospital: Hospital,
    banks: list[BloodBank],
    units: list[InventoryUnit],
) -> InventoryMatchResult:
    """
    Find and rank compatible inventory for a request.

    Processing:

    1. Filter inventory to AVAILABLE units.
    2. Filter by compatible blood group.
    3. Filter by requested component.
    4. Apply the cold-chain expiry buffer.
    5. Group eligible units by blood bank.
    6. Calculate hospital-to-bank distance and ETA.
    7. Rank banks by ETA.
    8. Select exact unit IDs nearest-first.
    9. Prefer earliest-expiring units within each bank (FEFO).
    10. Calculate inventory fulfillment and remaining shortfall.

    This function NEVER changes InventoryUnit.status.

    `selected_unit_ids` is only a proposed allocation. Actual reservation
    happens later after the recommendation/approval workflow.
    """

    acceptable_groups = acceptable_donor_groups(
        request.group,
        request.component,
    )

    bank_by_id = {
        bank.bank_id: bank
        for bank in banks
    }

    # Units must have at least this much remaining shelf life beyond the
    # request's required-by time.
    min_expiry = request.required_by + timedelta(
        days=COLD_CHAIN_BUFFER_DAYS
    )

    # Keep the actual units rather than only counting them.
    #
    # This allows the matching layer to return exact unit IDs while remaining
    # completely read-only.
    units_by_bank: dict[str, list[InventoryUnit]] = {}

    for unit in units:
        if unit.status != InventoryStatus.AVAILABLE:
            continue

        if unit.component != request.component:
            continue

        if unit.group not in acceptable_groups:
            continue

        if unit.expires_at < min_expiry:
            continue

        units_by_bank.setdefault(unit.bank_id, []).append(unit)

    # Prefer units that expire sooner first within each bank.
    # This is FEFO (First Expire, First Out) and reduces expiry waste.
    for bank_units in units_by_bank.values():
        bank_units.sort(key=lambda unit: unit.expires_at)

    matches: list[InventoryMatch] = []

    for bank_id, bank_units in units_by_bank.items():
        bank = bank_by_id.get(bank_id)

        # Ignore inventory whose bank is not represented in the supplied
        # BloodBank records.
        if bank is None:
            continue

        distance = haversine_km(
            hospital.geo,
            bank.geo,
        )

        matches.append(
            InventoryMatch(
                bank_id=bank_id,
                units_available=len(bank_units),
                selected_unit_ids=[],
                distance_km=round(distance, 2),
                eta_min=round(eta_minutes(hospital.geo, bank.geo), 1),
                reserved=False,
            )
        )

    # Nearest/fastest blood banks are considered first.
    matches.sort(key=lambda match: match.eta_min)

    remaining = request.qty

    # Greedily select exact units from the nearest banks.
    #
    # Important:
    #   selected_unit_ids != reserved units
    #
    # These IDs represent the proposed fulfillment plan only.
    for match in matches:
        if remaining <= 0:
            break

        bank_units = units_by_bank[match.bank_id]

        take = min(
            len(bank_units),
            remaining,
        )

        match.selected_unit_ids = [
            unit.unit_id
            for unit in bank_units[:take]
        ]

        remaining -= take

    units_from_inventory = request.qty - remaining

    return InventoryMatchResult(
        matches=matches,
        units_from_inventory=units_from_inventory,
        units_remaining=remaining,
    )