"""Local orchestration for the inventory reservation approval boundary.

This module connects the already-tested read-only match result to the
reservation mutation layer without allowing matching to mutate inventory.

Flow:
    case.ranked
      -> reservation.proposed (when inventory was selected)
      -> approval.decided
      -> inventory.reserved (only on approval)
      -> case.ranked for swarm (remaining donor shortfall)

If no inventory units were selected, the case goes directly to swarm.
"""

from __future__ import annotations

from contracts.events import (
    ApprovalDecidedPayload,
    EventEnvelope,
    EventType,
    ReservationProposedPayload,
)
from contracts.models import ApprovalDecision, Case, InventoryMatch
from approval_flow import ReservationApprovalService


class ReservationOrchestrationError(Exception):
    """Raised when the reservation orchestration receives an invalid event."""


class ReservationOrchestrator:
    """Connect matching, approval, reservation, and swarm boundaries."""

    def __init__(self, approval_service: ReservationApprovalService) -> None:
        self.approval_service = approval_service

    def handle_case_ranked(
        self, event: EventEnvelope
    ) -> EventEnvelope | None:
        if event.event_type != EventType.CASE_RANKED:
            raise ReservationOrchestrationError(
                f"Unsupported event type: {event.event_type}"
            )

        case = event.payload.case
        ranked_donors = list(event.payload.ranked_donors)
        selected_matches = [match for match in case.inventory_matches if match.selected_unit_ids]

        # No inventory proposal means there is nothing to approve or mutate.
        # The case can proceed directly to swarm using its already-computed
        # units_from_donors_remaining shortfall.
        if not selected_matches:
            return event

        proposals = [self.approval_service.propose(case, match, ranked_donors=ranked_donors)
                     for match in selected_matches]
        return proposals[0]

    def handle_approval(
        self, event: EventEnvelope[ApprovalDecidedPayload]
    ) -> EventEnvelope | None:
        if event.event_type != EventType.APPROVAL_DECIDED:
            raise ReservationOrchestrationError(
                f"Unsupported event type: {event.event_type}"
            )

        reserved_or_rejected = self.approval_service.handle_approval(event)
        if reserved_or_rejected is None:
            return None

        if reserved_or_rejected.event_type != EventType.INVENTORY_RESERVED:
            # Rejection is terminal for this reservation proposal. In
            # particular, do not silently send the same proposed inventory to
            # the swarm as though it had been reserved.
            return reserved_or_rejected

        case = reserved_or_rejected.payload.case
        ranked_donors = reserved_or_rejected.payload.ranked_donors
        recommendations = [self.approval_service.recommendations[rec_id]
                           for rec_id in case.reservation_recommendation_ids]
        if any(rec.state != "APPROVED" for rec in recommendations):
            from contracts.models import ReservationState
            case.reservation_state = ReservationState.AWAITING_APPROVAL
            return reserved_or_rejected

        # Re-emit the case to the swarm boundary only after the reservation
        # mutation has succeeded. The case's donor shortfall is intentionally
        # unchanged: it was already netted against the inventory match during
        # read-only matching.
        return EventEnvelope.case_ranked(
            request_id=case.request_id,
            case=case,
            ranked_donors=ranked_donors,
            correlation_id=event.correlation_id,
        )
