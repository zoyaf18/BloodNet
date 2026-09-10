"""Local approval boundary for inventory reservation proposals."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import psycopg

from contracts.audit import AuditRepository
from contracts.events import (
    ApprovalDecidedPayload,
    EventEnvelope,
    EventType,
    ReservationProposedPayload,
)
from contracts.models import (
    ApprovalDecision,
    AuditRecord,
    Case,
    InventoryMatch,
    InventoryReservation,
    InventoryReservationProposal,
    Recommendation,
    ReservationState,
    Notification,
)
from inventory_mutation import InventoryRepository, reserve_inventory


class ApprovalFlowError(Exception):
    """Raised when an approval cannot legally transition a recommendation."""


def create_reservation_recommendation(
    case: Case,
    inventory_match: InventoryMatch,
    *,
    reservation_id: str,
    expires_at: datetime | None = None,
) -> Recommendation:
    """Create a read-only reservation proposal from a match result."""
    if not inventory_match.selected_unit_ids:
        raise ApprovalFlowError("Cannot propose a reservation with no selected units.")

    proposal = InventoryReservationProposal(
        case_id=case.case_id,
        request_id=case.request_id,
        bank_id=inventory_match.bank_id,
        unit_ids=list(inventory_match.selected_unit_ids),
        reservation_id=reservation_id,
        expires_at=expires_at,
    )
    recommendation = Recommendation(
        rec_id=f"REC-{reservation_id}",
        type="INVENTORY_RESERVATION",
        case_id=case.case_id,
        request_id=case.request_id,
        payload={
            "bank_id": proposal.bank_id,
            "unit_ids": proposal.unit_ids,
            "reservation_id": proposal.reservation_id,
        },
        expected_impact={"units_reserved": len(proposal.unit_ids)},
        reservation_proposal=proposal,
    )
    case.reservation_recommendation_ids.append(recommendation.rec_id)
    case.reservation_state = ReservationState.AWAITING_APPROVAL
    return recommendation


class ReservationApprovalService:
    """Coordinates proposal and approval events around inventory mutation."""

    def __init__(
        self,
        repository: InventoryRepository,
        audit: AuditRepository | None = None,
        outbox=None,  # Optional: postgres_notification_outbox.PostgreSQLNotificationOutbox
        authorize_actor: Callable[[str], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.audit = audit or AuditRepository()
        self.outbox = outbox
        self.authorize_actor = authorize_actor or (lambda actor: bool(actor))
        self.recommendations: dict[str, Recommendation] = {}
        self.cases: dict[str, Case] = {}
        self._processed_approval_ids: set[str] = set()
        self.ranked_donors_by_case: dict[str, list[dict]] = {}

    def propose(
        self, case: Case, inventory_match: InventoryMatch, *, reservation_id: str | None = None, ranked_donors: list[dict] | None = None
    ) -> EventEnvelope[ReservationProposedPayload]:
        reservation_id = reservation_id or f"RES-{case.case_id}-{inventory_match.bank_id}"
        recommendation = create_reservation_recommendation(
            case, inventory_match, reservation_id=reservation_id
        )
        self.cases[case.case_id] = case
        self.recommendations[recommendation.rec_id] = recommendation
        self.ranked_donors_by_case[case.case_id] = list(ranked_donors or [])
        return EventEnvelope.reservation_proposed(
            case,
            recommendation,
            ranked_donors=self.ranked_donors_by_case[case.case_id],
        )

    def handle_approval(
        self, event: EventEnvelope[ApprovalDecidedPayload]
    ) -> EventEnvelope | None:
        if event.event_type != EventType.APPROVAL_DECIDED:
            raise ValueError(f"Unsupported event type: {event.event_type}")

        approval = event.payload.approval
        if not self.authorize_actor(approval.actor):
            raise ApprovalFlowError("Approval actor is not authorized for this recommendation.")
        if approval.approval_id in self._processed_approval_ids:
            return None

        recommendation = self.recommendations.get(approval.rec_id)
        if recommendation is None:
            recommendation = event.payload.recommendation
            self.recommendations[recommendation.rec_id] = recommendation

        case = self._case_for(recommendation)
        if recommendation.state != "AWAITING_APPROVAL":
            raise ApprovalFlowError(
                f"Recommendation '{recommendation.rec_id}' is already {recommendation.state}."
            )

        proposal = recommendation.reservation_proposal
        if proposal is None:
            raise ApprovalFlowError("Approval does not reference a reservation proposal.")

        # Use transaction to ensure approval state, audit, and outbox are all-or-nothing
        with self.repository.transaction() as connection:
            cursor = connection.cursor() if hasattr(connection, "cursor") else None
            decision = "APPROVED" if approval.decision == ApprovalDecision.APPROVE else "REJECTED"
            audit_record = AuditRecord(
                audit_id=f"AUDIT-{case.case_id}-{event.event_id}",
                action="approval_approved" if decision == "APPROVED" else "approval_rejected",
                request_id=case.request_id,
                case_id=case.case_id,
                event_id=event.event_id,
                actor=approval.actor,
                at=datetime.now(timezone.utc),
            )
            if cursor is not None and decision == "REJECTED":
                self.audit.append(audit_record, cursor=cursor)
                claimed = self.repository.claim_approval_decision(
                    cursor=cursor,
                    approval_id=approval.approval_id,
                    rec_id=recommendation.rec_id,
                    case_id=case.case_id,
                    decision=decision,
                    audit_id=audit_record.audit_id,
                    outbox_event_id=None,
                )
                if not claimed:
                    cursor.execute(
                        "DELETE FROM audit_records WHERE audit_id = %s",
                        (audit_record.audit_id,),
                    )
                    return None
            elif approval.approval_id in self._processed_approval_ids:
                return None
            else:
                self._processed_approval_ids.add(approval.approval_id)
                self.audit.append(audit_record)

            if approval.decision == ApprovalDecision.REJECT:
                recommendation.state = "REJECTED"
                case.reservation_state = ReservationState.REJECTED
                if cursor is not None:
                    cursor.close()
                return EventEnvelope.reservation_rejected(
                    case, recommendation, approval, correlation_id=event.correlation_id
                )

            # APPROVED path
            outbox_event_id = None
            # Approval is an audit event. Only actual donor outreach belongs in the delivery outbox.

            if cursor is not None:
                self.audit.append(audit_record, cursor=cursor)
                claimed = self.repository.claim_approval_decision(
                    cursor=cursor,
                    approval_id=approval.approval_id,
                    rec_id=recommendation.rec_id,
                    case_id=case.case_id,
                    decision="APPROVED",
                    audit_id=audit_record.audit_id,
                    outbox_event_id=outbox_event_id,
                )
                if not claimed:
                    cursor.execute(
                        "DELETE FROM audit_records WHERE audit_id = %s",
                        (audit_record.audit_id,),
                    )
                    return None

            reservation = reserve_inventory(
                self.repository,
                case_id=proposal.case_id,
                request_id=proposal.request_id,
                bank_id=proposal.bank_id,
                unit_ids=proposal.unit_ids,
                expires_at=proposal.expires_at,
                reservation_id=proposal.reservation_id,
            )
            recommendation.state = "APPROVED"
            case.reservation_state = ReservationState.RESERVED
            case.reservation_ids.append(reservation.reservation_id)

            if cursor is not None:
                cursor.close()
            
            ranked_donors = list(getattr(event.payload, "ranked_donors", []))
            if not ranked_donors:
                ranked_donors = self.ranked_donors_by_case.get(case.case_id, [])
            return EventEnvelope.inventory_reserved(
                case,
                recommendation,
                reservation,
                ranked_donors=ranked_donors,
                correlation_id=event.correlation_id,
            )

    def _case_for(self, recommendation: Recommendation) -> Case:
        proposal = recommendation.reservation_proposal
        assert proposal is not None
        case = self.cases.get(proposal.case_id)
        if case is None:
            case = Case(case_id=proposal.case_id, request_id=proposal.request_id)
            self.cases[case.case_id] = case
        return case
