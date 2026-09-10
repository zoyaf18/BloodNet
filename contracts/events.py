"""Shared event contracts for the local and future Pub/Sub event boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

from contracts.models import (
    Approval,
    Case,
    InventoryReservation,
    Notification,
    Recommendation,
    Request,
    OutreachResponse,
    AuditRecord,
)

T = TypeVar("T")


class EventType(str, Enum):
    REQUEST_CREATED = "request.created"
    CASE_RANKED = "case.ranked"
    RECOMMENDATION_CREATED = "recommendation.created"
    NOTIFICATION_REQUESTED = "notification.requested"
    AUDIT_EVENT = "audit.event"
    RESERVATION_PROPOSED = "reservation.proposed"
    APPROVAL_DECIDED = "approval.decided"
    INVENTORY_RESERVED = "inventory.reserved"
    RESERVATION_REJECTED = "reservation.rejected"
    NOTIFICATIONS_REQUESTED = "notifications.requested"
    NOTIFICATION_SENT = "notification.sent"
    INVENTORY_CONSUMED = "inventory.consumed"
    DONOR_RESPONSE_RECEIVED = "donor.response_received"
    FULFILLMENT_COMPLETED = "fulfillment.completed"


class RequestCreatedPayload(BaseModel):
    request: Request


class CaseRankedPayload(BaseModel):
    case: Case
    ranked_donors: list[dict[str, Any]] = Field(default_factory=list)


class RecommendationCreatedPayload(BaseModel):
    recommendation: Recommendation


class NotificationRequestedPayload(BaseModel):
    case: Case
    donor_ids: list[str] = Field(default_factory=list)


class AuditEventPayload(BaseModel):
    record: AuditRecord


class ReservationProposedPayload(BaseModel):
    case: Case
    recommendation: Recommendation
    ranked_donors: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalDecidedPayload(BaseModel):
    approval: Approval
    recommendation: Recommendation
    ranked_donors: list[dict[str, Any]] = Field(default_factory=list)


class InventoryReservedPayload(BaseModel):
    case: Case
    recommendation: Recommendation
    reservation: InventoryReservation
    ranked_donors: list[dict[str, Any]] = Field(default_factory=list)


class ReservationRejectedPayload(BaseModel):
    case: Case
    recommendation: Recommendation
    approval: Approval


class NotificationsRequestedPayload(BaseModel):
    case: Case
    donor_ids: list[str] = Field(default_factory=list)


class NotificationSentPayload(BaseModel):
    notification: Notification


class DonorResponsePayload(BaseModel):
    case: Case
    case_id: str
    outreach_id: str | None = None
    donor_id: str
    response: OutreachResponse
    units: int = 1


class InventoryConsumedPayload(BaseModel):
    case: Case
    reservation: InventoryReservation


class FulfillmentCompletedPayload(BaseModel):
    case: Case


class EventEnvelope(BaseModel, Generic[T]):
    event_id: str = Field(default_factory=lambda: f"EVT-{uuid4().hex}")
    event_type: EventType
    schema_version: str = "1.0"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: str
    correlation_id: str
    payload: T

    @classmethod
    def request_created(cls, request: Request) -> "EventEnvelope[RequestCreatedPayload]":
        return cls(
            event_type=EventType.REQUEST_CREATED,
            request_id=request.request_id,
            correlation_id=request.request_id,
            payload=RequestCreatedPayload(request=request),
        )

    @classmethod
    def recommendation_created(
        cls, recommendation: Recommendation, *, correlation_id: str | None = None
    ) -> "EventEnvelope[RecommendationCreatedPayload]":
        return cls(
            event_type=EventType.RECOMMENDATION_CREATED,
            request_id=recommendation.request_id or recommendation.rec_id,
            correlation_id=correlation_id or recommendation.case_id or recommendation.rec_id,
            payload=RecommendationCreatedPayload(recommendation=recommendation),
        )

    @classmethod
    def notification_requested(
        cls, case: Case, donor_ids: list[str], *, correlation_id: str
    ) -> "EventEnvelope[NotificationRequestedPayload]":
        return cls(
            event_type=EventType.NOTIFICATION_REQUESTED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=NotificationRequestedPayload(case=case, donor_ids=donor_ids),
        )

    @classmethod
    def audit_event(
        cls, record: AuditRecord, *, correlation_id: str | None = None
    ) -> "EventEnvelope[AuditEventPayload]":
        return cls(
            event_type=EventType.AUDIT_EVENT,
            request_id=record.request_id,
            correlation_id=correlation_id or record.case_id or record.request_id,
            payload=AuditEventPayload(record=record),
        )

    @classmethod
    def case_ranked(
        cls,
        request_id: str,
        case: Case,
        ranked_donors: list[dict[str, Any]],
        *,
        correlation_id: str | None = None,
    ) -> "EventEnvelope[CaseRankedPayload]":
        return cls(
            event_type=EventType.CASE_RANKED,
            request_id=request_id,
            correlation_id=correlation_id or request_id,
            payload=CaseRankedPayload(case=case, ranked_donors=ranked_donors),
        )

    @classmethod
    def reservation_proposed(
        cls, case: Case, recommendation: Recommendation, *, ranked_donors: list[dict[str, Any]] | None = None, correlation_id: str | None = None
    ) -> "EventEnvelope[ReservationProposedPayload]":
        return cls(
            event_type=EventType.RESERVATION_PROPOSED,
            request_id=case.request_id,
            correlation_id=correlation_id or case.case_id,
            payload=ReservationProposedPayload(
                case=case, recommendation=recommendation, ranked_donors=ranked_donors or []
            ),
        )

    @classmethod
    def approval_decided(
        cls, approval: Approval, recommendation: Recommendation, *, ranked_donors: list[dict[str, Any]] | None = None, correlation_id: str
    ) -> "EventEnvelope[ApprovalDecidedPayload]":
        proposal = recommendation.reservation_proposal
        return cls(
            event_type=EventType.APPROVAL_DECIDED,
            request_id=proposal.request_id if proposal else recommendation.rec_id,
            correlation_id=correlation_id,
            payload=ApprovalDecidedPayload(
                approval=approval, recommendation=recommendation, ranked_donors=ranked_donors or []
            ),
        )

    @classmethod
    def inventory_reserved(
        cls, case: Case, recommendation: Recommendation, reservation: InventoryReservation,
        *, ranked_donors: list[dict[str, Any]] | None = None, correlation_id: str,
    ) -> "EventEnvelope[InventoryReservedPayload]":
        return cls(
            event_type=EventType.INVENTORY_RESERVED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=InventoryReservedPayload(
                case=case,
                recommendation=recommendation,
                reservation=reservation,
                ranked_donors=ranked_donors or [],
            ),
        )

    @classmethod
    def reservation_rejected(
        cls, case: Case, recommendation: Recommendation, approval: Approval,
        *, correlation_id: str,
    ) -> "EventEnvelope[ReservationRejectedPayload]":
        return cls(
            event_type=EventType.RESERVATION_REJECTED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=ReservationRejectedPayload(
                case=case, recommendation=recommendation, approval=approval
            ),
        )

    @classmethod
    def notifications_requested(
        cls, case: Case, donor_ids: list[str], *, correlation_id: str
    ) -> "EventEnvelope[NotificationsRequestedPayload]":
        return cls(
            event_type=EventType.NOTIFICATIONS_REQUESTED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=NotificationsRequestedPayload(case=case, donor_ids=donor_ids),
        )

    @classmethod
    def notification_sent(
        cls, notification: Notification, *, correlation_id: str
    ) -> "EventEnvelope[NotificationSentPayload]":
        return cls(
            event_type=EventType.NOTIFICATION_SENT,
            request_id=notification.request_id,
            correlation_id=correlation_id,
            payload=NotificationSentPayload(notification=notification),
        )

    @classmethod
    def donor_response(
        cls, case: Case, donor_id: str, response: OutreachResponse, *, units: int = 1,
        outreach_id: str | None = None,
        correlation_id: str,
    ) -> "EventEnvelope[DonorResponsePayload]":
        return cls(
            event_type=EventType.DONOR_RESPONSE_RECEIVED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=DonorResponsePayload(
                case=case,
                case_id=case.case_id,
                outreach_id=outreach_id,
                donor_id=donor_id,
                response=response,
                units=units,
            ),
        )

    @classmethod
    def inventory_consumed(
        cls, case: Case, reservation: InventoryReservation, *, correlation_id: str
    ) -> "EventEnvelope[InventoryConsumedPayload]":
        return cls(
            event_type=EventType.INVENTORY_CONSUMED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=InventoryConsumedPayload(case=case, reservation=reservation),
        )

    @classmethod
    def fulfillment_completed(
        cls, case: Case, *, correlation_id: str
    ) -> "EventEnvelope[FulfillmentCompletedPayload]":
        return cls(
            event_type=EventType.FULFILLMENT_COMPLETED,
            request_id=case.request_id,
            correlation_id=correlation_id,
            payload=FulfillmentCompletedPayload(case=case),
        )
