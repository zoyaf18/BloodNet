"""Local fulfillment orchestration after approval and donor responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import uuid4

from contracts.audit import AuditRepository
from contracts.events import (
    DonorResponsePayload,
    EventEnvelope,
    EventType,
    InventoryReservedPayload,
)
from contracts.models import (
    AuditRecord,
    Case,
    CaseOutcome,
    OutreachResponse,
    Recommendation,
)
from inventory_mutation import InventoryRepository, consume_reservation
from processed_event_repository import (
    InMemoryProcessedEventRepository,
    ProcessedEventRepository,
)


class DonorResponseStore(Protocol):
    def save_response(self, outreach_id: str, donor_id: str, response: str, responded_at: datetime) -> None: ...

    def mark_processed(self, outreach_id: str, donor_id: str) -> None: ...


class FulfillmentService:
    def __init__(
        self,
        repository: InventoryRepository,
        audit: AuditRepository | None = None,
        processed_event_repository: ProcessedEventRepository | None = None,
        donor_response_store: DonorResponseStore | None = None,
        case_persister: Callable[[Case], None] | None = None,
        escalation_persister: Callable[[Recommendation], None] | None = None,
    ) -> None:
        self.repository = repository
        self.audit = audit or AuditRepository()
        self.cases: dict[str, Case] = {}
        self.processed_event_repository = (
            processed_event_repository or InMemoryProcessedEventRepository()
        )
        self._responses: dict[str, set[str]] = {}
        self.donor_response_store = donor_response_store
        self.case_persister = case_persister
        self.escalation_persister = escalation_persister

    def handle_inventory_reserved(
        self, event: EventEnvelope[InventoryReservedPayload]
    ) -> EventEnvelope:
        if event.event_type != EventType.INVENTORY_RESERVED:
            raise ValueError(f"Unsupported event type: {event.event_type}")
        if hasattr(self.repository, "_transaction_connection"):
            with self.repository.transaction() as transaction:
                if not self.processed_event_repository.try_claim(
                    event.event_id,
                    event.event_type.value,
                    connection=transaction,
                ):
                    return EventEnvelope.inventory_consumed(
                        event.payload.case,
                        event.payload.reservation,
                        correlation_id=event.correlation_id,
                    )
                case = event.payload.case
                reservation = consume_reservation(
                    self.repository, event.payload.reservation.reservation_id
                )
        else:
            if not self.processed_event_repository.try_claim(
                event.event_id,
                event.event_type.value,
            ):
                return EventEnvelope.inventory_consumed(
                    event.payload.case,
                    event.payload.reservation,
                    correlation_id=event.correlation_id,
                )
            case = event.payload.case
            reservation = consume_reservation(
                self.repository, event.payload.reservation.reservation_id
            )
        self.cases[case.case_id] = case
        self._audit(
            "inventory_consumed",
            case,
            event.event_id,
            details={"reservation_id": reservation.reservation_id, "units": len(reservation.unit_ids)},
        )
        return EventEnvelope.inventory_consumed(
            case, reservation, correlation_id=event.correlation_id
        )

    def handle_donor_response(
        self, event: EventEnvelope[DonorResponsePayload]
    ) -> EventEnvelope | None:
        if event.event_type != EventType.DONOR_RESPONSE_RECEIVED:
            raise ValueError(f"Unsupported event type: {event.event_type}")
        payload = event.payload
        if not self.processed_event_repository.try_claim(
            event.event_id, event.event_type.value
        ):
            return None

        response_id = payload.outreach_id or f"CASE-{payload.case_id}"
        if self.donor_response_store is not None:
            self.donor_response_store.save_response(
                response_id,
                payload.donor_id,
                payload.response.value,
                event.occurred_at,
            )
        if payload.response != OutreachResponse.ACCEPT:
            case = self.cases.setdefault(payload.case_id, payload.case)
            case.outcome = CaseOutcome.PARTIALLY_FULFILLED
            case.escalation_state = "required"
            remaining_shortfall = max(
                int(getattr(case, "donor_target_units", 0) or getattr(case, "units_from_donors_remaining", 0))
                - int(getattr(case, "units_from_donors_fulfilled", 0) or 0),
                0,
            )
            escalation = Recommendation(
                rec_id=f"ESC-{case.case_id}-{remaining_shortfall}",
                type="MOBILIZE_DONORS",
                request_id=case.request_id,
                case_id=case.case_id,
                payload={
                    "target_units": remaining_shortfall,
                    "trigger": "donor_response",
                    "parent_case_id": case.case_id,
                    "response": payload.response.value,
                },
                rationale="A donor declined or did not respond, so the donor cohort needs another outreach round.",
                expected_impact={"units_needed": remaining_shortfall},
                provenance={"source": "deterministic_outcome_loop"},
                state="AWAITING_APPROVAL",
            )
            if self.escalation_persister is not None:
                self.escalation_persister(escalation)
            if self.case_persister is not None:
                self.case_persister(case)
            if self.donor_response_store is not None:
                self.donor_response_store.mark_processed(response_id, payload.donor_id)
            return None

        accepted = self._responses.setdefault(payload.case_id, set())
        response_key = f"{response_id}:{payload.donor_id}"
        if response_key in accepted:
            return None
        accepted.add(response_key)
        case = self.cases.setdefault(payload.case_id, payload.case)
        case.units_from_donors_fulfilled = min(
            case.units_from_donors_remaining,
            case.units_from_donors_fulfilled + max(payload.units, 0),
        )
        self._audit(
            "donor_units_fulfilled",
            case,
            event.event_id,
            details={"donor_id": payload.donor_id, "units": payload.units},
        )
        total = case.units_from_inventory + case.units_from_donors_fulfilled
        remaining_shortfall = max(
            case.units_from_donors_remaining - case.units_from_donors_fulfilled,
            0,
        )
        if remaining_shortfall:
            case.outcome = CaseOutcome.PARTIALLY_FULFILLED
            case.escalation_state = "required"
            escalation = Recommendation(
                rec_id=f"ESC-{case.case_id}-{remaining_shortfall}",
                type="MOBILIZE_DONORS",
                request_id=case.request_id,
                case_id=case.case_id,
                payload={
                    "target_units": remaining_shortfall,
                    "trigger": "donor_response",
                    "parent_case_id": case.case_id,
                },
                rationale="A donor response was recorded but the case remains below its required donor coverage.",
                expected_impact={"units_needed": remaining_shortfall},
                provenance={"source": "deterministic_outcome_loop"},
                state="AWAITING_APPROVAL",
            )
            if self.escalation_persister is not None:
                self.escalation_persister(escalation)
        if total < case.units_from_inventory + case.units_from_donors_remaining:
            if self.case_persister is not None:
                self.case_persister(case)
            if self.donor_response_store is not None:
                self.donor_response_store.mark_processed(response_id, payload.donor_id)
            return None
        case.outcome = CaseOutcome.FULFILLED
        case.escalation_state = "not_required"
        self._audit("case_fulfilled", case, event.event_id, details={"units": total})
        if self.case_persister is not None:
            self.case_persister(case)
        if self.donor_response_store is not None:
            self.donor_response_store.mark_processed(response_id, payload.donor_id)
        return EventEnvelope.fulfillment_completed(
            case, correlation_id=event.correlation_id
        )

    def _audit(self, action: str, case: Case, event_id: str, *, details: dict) -> None:
        self.audit.append(
            AuditRecord(
                audit_id=f"AUDIT-{uuid4().hex}",
                action=action,
                request_id=case.request_id,
                case_id=case.case_id,
                event_id=event_id,
                details=details,
                at=datetime.now(timezone.utc),
            )
        )