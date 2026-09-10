"""Event handler for the swarm stage of the local BloodNet hot path."""
from __future__ import annotations

from typing import Any

from contracts.events import CaseRankedPayload, EventEnvelope, EventType
from contracts.observability import configure_logging, log_event
from escalation import AdaptiveSwarmPolicy, EscalationEngine


class SwarmEventHandler:
    def __init__(self, engine: EscalationEngine | None = None) -> None:
        self.engine = engine or EscalationEngine(match_svc_url="http://match-svc:8000")
        self.logger = configure_logging()
        self.policy = AdaptiveSwarmPolicy()
        self.rounds: dict[str, int] = {}
        self.accepted: dict[str, int] = {}

    def next_round(
        self,
        event: EventEnvelope[CaseRankedPayload],
        *,
        accepted: int,
        pending_ids: set[str] | None = None,
    ) -> EventEnvelope[Any] | None:
        """Recalculate outreach after responses and widen the radius if needed."""
        case_id = event.payload.case.case_id
        self.rounds[case_id] = self.rounds.get(case_id, 1) + 1
        self.accepted[case_id] = accepted
        return self.handle_case_ranked(
            event, accepted=accepted, pending_ids=pending_ids
        )

    def handle_case_ranked(
        self,
        event: EventEnvelope[CaseRankedPayload],
        *,
        accepted: int = 0,
        pending_ids: set[str] | None = None,
    ) -> EventEnvelope[Any] | None:
        if event.event_type != EventType.CASE_RANKED:
            raise ValueError(f"Unsupported event type: {event.event_type}")

        payload = event.payload
        case = payload.case
        # The case already contains the inventory-derived shortfall. The swarm
        # handler deliberately does not recalculate it from the original request.
        units_remaining = case.units_from_donors_remaining

        log_event(
            self.logger,
            "case_received_by_swarm",
            service="swarm-svc",
            event_id=event.event_id,
            request_id=event.request_id,
            case_id=case.case_id,
            units_from_inventory=case.units_from_inventory,
            units_remaining=units_remaining,
        )

        # This phase keeps the handler informational: actual bank/donor records
        # are supplied by the caller in the HTTP API. The local event flow uses
        # the same deterministic cohort-sizing policy with the ranked donors.
        if units_remaining == 0:
            result = {
                "status": "inventory_covered",
                "target_units": 0,
                "cohort_size": 0,
                "donors_contacted": [],
            }
        else:
            donor_count = len(payload.ranked_donors)
            probabilities = [
                float(d.get("success_probability", 0.0))
                for d in payload.ranked_donors
            ]
            round_number = self.rounds.get(case.case_id, 1)
            selected_round = self.policy.plan(
                payload.ranked_donors,
                units_remaining,
                round_number=round_number,
                accepted=accepted,
                pending_ids=pending_ids,
            )
            selected_ids = set(selected_round.donor_ids) if selected_round else set()
            selected = [
                donor for donor in payload.ranked_donors
                if donor["donor_id"] in selected_ids
            ]
            result = {
                "status": "initiated" if selected else "unfulfilled",
                "target_units": units_remaining,
                "cohort_size": len(selected),
                "available_donors": donor_count,
                "donors_contacted": [d["donor_id"] for d in selected],
                "round": round_number,
                "radius_km": selected_round.radius_km if selected_round else None,
                "wait_seconds": selected_round.wait_seconds if selected_round else None,
            }

        log_event(
            self.logger,
            "swarm_decision_created",
            service="swarm-svc",
            request_id=event.request_id,
            case_id=case.case_id,
            **result,
        )
        donor_ids = result.get("donors_contacted", [])
        if not donor_ids:
            return None
        return EventEnvelope.notifications_requested(
            case,
            donor_ids,
            correlation_id=event.correlation_id,
        )
