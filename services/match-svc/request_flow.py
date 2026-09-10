"""Local match-service orchestration for the BloodNet emergency hot path.

This module composes the already-tested deterministic inventory and compatibility
logic with donor scoring. Eligibility is an explicit upstream gate: callers must
pass only donor IDs that have already passed the health-screening/eligibility
service. Ineligible donors are therefore never sent to the ML scorer.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import isfinite
from uuid import uuid4

from contracts.models import Case, Donor, Hospital, InventoryUnit, Request, Urgency
from contracts.travel_time import build_travel_time_provider
from compatibility import acceptable_donor_groups
from eligibility import EligibilityScreening, screen_donor
from inventory_match import find_inventory_matches
from scoring import score_donors

_MATCH_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _prepare_donor_context(
    donor: Donor,
    request: Request,
    hospital: Hospital,
    banks: list,
    compatible_pool_size: int,
    screening_age_years: int | None = None,
    travel_time_provider=None,
    distance_cache: dict[tuple[float, float], tuple[float, float]] | None = None,
) -> Donor:
    """Attach request-specific travel distance without mutating the donor."""
    from inventory_match import haversine_km
    travel_time_provider = travel_time_provider or build_travel_time_provider()

    features = dict(donor.reliability_features)
    donor_location = (donor.geo.lat, donor.geo.lng)
    if distance_cache is not None and donor_location in distance_cache:
        distance, travel_minutes = distance_cache[donor_location]
    else:
        bank_distances = [haversine_km(donor.geo, bank.geo) for bank in banks]
        distance = min(bank_distances) if bank_distances else haversine_km(donor.geo, hospital.geo)
        travel_minutes = travel_time_provider.minutes(donor.geo, hospital.geo)
        if distance_cache is not None:
            distance_cache[donor_location] = (distance, travel_minutes)
    features.update(
        {
            "age": screening_age_years if screening_age_years is not None else features.get("age", 40),
            "distance_to_bank_km": distance,
            "travel_time_min": travel_minutes,
            "hour_of_day": request.required_by.hour,
            "day_of_week": request.required_by.weekday(),
            "urgency_level": {
                Urgency.ROUTINE: 0,
                Urgency.HIGH: 1,
                Urgency.CRITICAL: 2,
            }[request.urgency],
            "group_scarcity_index": min(
                request.qty / max(compatible_pool_size, 1), 1.0
            ),
        }
    )
    return donor.model_copy(update={"reliability_features": features})


def _has_valid_coordinates(donor: Donor) -> bool:
    return (
        isfinite(donor.geo.lat)
        and isfinite(donor.geo.lng)
        and -90 <= donor.geo.lat <= 90
        and -180 <= donor.geo.lng <= 180
    )


class MatchFlowResult:
    def __init__(self, case: Case, ranked_donors: list[dict]):
        self.case = case
        self.ranked_donors = ranked_donors


def match_request(
    request: Request,
    hospital: Hospital,
    banks: list,
    inventory_units: list[InventoryUnit],
    donors: list[Donor],
    *,
    eligible_donor_ids: set[str] | None = None,
    eligibility_records: dict[str, EligibilityScreening] | None = None,
) -> MatchFlowResult:
    """Run the local Phase 2 matching flow.

    Inventory and donor branches are independent and are evaluated concurrently.
    The donor branch filters the supplied *eligible* pool by deterministic
    compatibility before ML scoring.
    """
    if eligible_donor_ids is None and eligibility_records is None:
        raise ValueError("eligible_donor_ids is required; eligibility must be an explicit gate")

    def inventory_branch():
        return find_inventory_matches(request, hospital, banks, inventory_units)

    acceptable_groups = acceptable_donor_groups(request.group, request.component)

    def donor_branch():
        travel_time_provider = build_travel_time_provider()
        # Both estimates depend on location within this one request. Reuse them
        # for colocated donors without sharing mutable donor features or routes
        # across requests with different destinations.
        distance_cache: dict[tuple[float, float], tuple[float, float]] = {}
        eligible_compatible = [
            donor
            for donor in donors
            if (eligible_donor_ids is None or donor.donor_id in eligible_donor_ids)
            and (
                eligibility_records is None
                or (
                    donor.donor_id in eligibility_records
                    and screen_donor(
                        donor,
                        request.component,
                        eligibility_records[donor.donor_id],
                        as_of=request.required_by,
                    ).eligible
                )
            )
            and donor.blood_group in acceptable_groups
            and _has_valid_coordinates(donor)
        ]
        contextual_donors = [
            _prepare_donor_context(
                donor, request, hospital, banks, len(eligible_compatible),
                eligibility_records[donor.donor_id].age_years
                if eligibility_records is not None and donor.donor_id in eligibility_records else None,
                travel_time_provider=travel_time_provider, distance_cache=distance_cache,
            )
            for donor in eligible_compatible
        ]
        return contextual_donors, score_donors(contextual_donors), {
            donor.donor_id: donor.reliability_features for donor in contextual_donors
        }

    inventory_future = _MATCH_EXECUTOR.submit(inventory_branch)
    donor_future = _MATCH_EXECUTOR.submit(donor_branch)
    inventory_result = inventory_future.result()
    eligible_compatible, scores, donor_context = donor_future.result()

    ranked = sorted(
        zip(eligible_compatible, scores),
        key=lambda pair: pair[1],
        reverse=True,
    )

    ranked_donors = [
        {
            "donor_id": donor.donor_id,
            "blood_group": donor.blood_group.value,
            "success_probability": round(float(prob), 6),
            "distance_to_bank_km": round(float(donor_context.get(donor.donor_id, {}).get("distance_to_bank_km", 0)), 2),
            "travel_time_min": round(float(donor_context.get(donor.donor_id, {}).get("travel_time_min", 0)), 1),
            "eligibility": "eligible",
            "explanation": "Eligible blood-group match ranked by predicted response probability and travel time.",
        }
        for donor, prob in ranked
    ]

    case = Case(
        case_id=f"CASE-{uuid4().hex[:12]}",
        request_id=request.request_id,
        inventory_matches=inventory_result.matches,
        units_from_inventory=inventory_result.units_from_inventory,
        units_from_donors_remaining=inventory_result.units_remaining,
        donor_target_units=inventory_result.units_remaining,
        # Swarm recomputes the live fulfillment probability after cohort sizing.
        fulfillment_probability=0.0,
        escalation_state=(
            "not_started" if inventory_result.units_remaining > 0 else "inventory_covered"
        ),
    )

    return MatchFlowResult(case=case, ranked_donors=ranked_donors)
