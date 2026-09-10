"""Escalation policy for the donor swarm.

Inventory matching is performed by match-svc. Swarm sizing therefore works
from the remaining shortfall rather than the original request quantity.
"""

from dataclasses import dataclass
from typing import Any, List

from contracts.models import BloodBank, Donor, Request
from poisson_binomial import calculate_cohort_size


@dataclass(frozen=True)
class SwarmRound:
    number: int
    radius_km: float
    wait_seconds: int
    donor_ids: list[str]


class AdaptiveSwarmPolicy:
    RADII = (2.0, 5.0, 15.0, 50.0)
    WAITS = (240, 360, 600, 900)

    def __init__(self, confidence: float = 0.95) -> None:
        self.confidence = confidence

    def plan(self, ranked_donors: list[dict[str, Any]], target_units: int, *, round_number: int = 1, accepted: int = 0, pending_ids: set[str] | None = None) -> SwarmRound | None:
        if target_units <= accepted or not ranked_donors:
            return None
        pending_ids = pending_ids or set()
        candidates = [d for d in ranked_donors if d["donor_id"] not in pending_ids]

        def adjusted_probability(donor: dict[str, Any]) -> float:
            probability = float(donor.get("success_probability", 0.0))
            probability -= min(float(donor.get("contact_fatigue_30d", 0)), 4) * 0.03
            probability += float(donor.get("exploration_bonus", 0.0))
            return max(0.0, min(1.0, probability))

        # Python's stable sort preserves the upstream fair/round-robin order
        # for equal probabilities.
        candidates.sort(key=adjusted_probability, reverse=True)
        cohort_size = calculate_cohort_size(
            [adjusted_probability(donor) for donor in candidates],
            target_units - accepted,
            confidence=self.confidence,
        )
        index = min(max(round_number - 1, 0), len(self.RADII) - 1)
        return SwarmRound(
            number=round_number,
            radius_km=self.RADII[index],
            wait_seconds=self.WAITS[index],
            donor_ids=[donor["donor_id"] for donor in candidates[:cohort_size]],
        )


class EscalationEngine:
    def __init__(self, match_svc_url: str):
        # Kept as configuration for the future event/HTTP integration. Phase 2.1
        # accepts match results as inputs rather than making hidden network calls.
        self.match_svc_url = match_svc_url

    def escalate(
        self,
        req: Request,
        available_banks: List[BloodBank],
        available_donors: List[Donor],
        units_from_inventory: int = 0,
    ) -> dict[str, Any]:
        """Execute the current escalation ladder using an inventory-aware shortfall.

        ``units_from_inventory`` is supplied by the inventory branch of match-svc.
        This keeps the swarm decision deterministic and prevents donor outreach
        for units already covered by on-hand inventory.
        """
        if units_from_inventory < 0:
            raise ValueError("units_from_inventory cannot be negative")
        if units_from_inventory > req.qty:
            raise ValueError("units_from_inventory cannot exceed requested quantity")

        units_remaining = req.qty - units_from_inventory

        # Inventory fully covers the request: no donor mobilization is needed.
        if units_remaining == 0:
            return {
                "level": "network",
                "status": "fulfilled",
                "target_units": 0,
                "units_from_inventory": units_from_inventory,
                "units_remaining": 0,
                "action": "proceed_to_inventory_approval",
            }

        # Network/hub escalation is informational at this stage. Actual inventory
        # allocation remains read-only until the future approval workflow lands.
        if available_banks:
            target_bank = available_banks[0]
            return {
                "level": "network",
                "status": "shortfall",
                "bank_id": target_bank.bank_id,
                "units_from_inventory": units_from_inventory,
                "units_remaining": units_remaining,
                "action": "continue_to_swarm",
            }

        # Swarm sizing is explicitly based on the remaining shortfall.
        probs = [0.8] * min(10, len(available_donors)) + [
            0.4
        ] * max(0, len(available_donors) - 10)
        cohort_size = calculate_cohort_size(
            probs, units_remaining, confidence=0.95
        )

        if cohort_size > 0 and cohort_size <= len(available_donors):
            selected_donors = available_donors[:cohort_size]
            return {
                "level": "swarm",
                "status": "initiated",
                "target_units": units_remaining,
                "units_from_inventory": units_from_inventory,
                "units_remaining": units_remaining,
                "cohort_size": cohort_size,
                "donors_contacted": [d.donor_id for d in selected_donors],
                "action": "trigger_communications",
            }

        return {
            "level": "failed",
            "status": "unfulfilled",
            "units_from_inventory": units_from_inventory,
            "units_remaining": units_remaining,
            "reason": "insufficient_donors",
        }
